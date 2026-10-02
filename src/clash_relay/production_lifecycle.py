"""Single application-layer owner for the production release lifecycle.

GitHub Actions is intentionally a thin adapter. This module owns production
execution order and calls package application services directly. The only
remaining process boundaries are true external programs such as Mihomo.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .builder import build_candidate
from .config_loader import ProjectDefinition
from .errors import (
    CandidateValidationStageError,
    ClashRelayError,
    CommitUnknownError,
    PublicationError,
    ValidationError,
)
from .mihomo import load_candidate
from .mihomo_download import download_pinned_mihomo
from .policy_document import load_policy_document
from .production_application import (
    load_ai_qualification_cache_state,
    load_scheduler_history_state,
    persist_ai_qualification_cache,
    persist_scheduler_history,
    reconcile_production_release,
    render_production_proof_application,
)
from .production_diagnostics import sanitize_source_admission_report
from .production_observability import (
    publish_post_release_observability,
    record_failure_observability,
)
from .production_pipeline import (
    ProductionPipelineOutputs,
    ProjectPaths,
    QualificationPaths,
    run_production_pipeline,
)
from .production_release_stage import (
    ReleaseCandidateStagePaths,
    run_release_candidate_stage,
)
from .publication import publication_gate
from .qualification_observability import safe_qualification_observability
from .release_manifest import build_release_manifest, render_release_manifest_markdown
from .release_reliability import ReleasePhase, ReleaseProgress
from .runtime_names import valid_source_id
from .source_quality import build_source_quality_report
from .util import atomic_write, atomic_write_bytes

_MAX_CARRIER_INPUT_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class ProductionLifecyclePaths:
    root: Path
    config: Path
    subscriptions: Path
    policies: Path
    promotion_guard: Path
    mihomo_manifest: Path
    work_dir: Path
    private_dir: Path
    public_dir: Path
    bin_dir: Path

    @classmethod
    def canonical(cls, root: Path) -> ProductionLifecyclePaths:
        root = root.resolve()
        work = root / ".work"
        return cls(
            root=root,
            config=root / "config.yaml",
            subscriptions=root / "subscriptions.yaml",
            policies=root / "policies.yaml",
            promotion_guard=root / "promotion-guard.yaml",
            mihomo_manifest=root / "tools/mihomo-versions.json",
            work_dir=work,
            private_dir=work / "private",
            public_dir=work / "public",
            bin_dir=work / "bin",
        )


def _tag_validation_stage(error: ValidationError, stage: str) -> None:
    if isinstance(error, CandidateValidationStageError):
        return
    current = getattr(error, "validation_stage", None)
    if not isinstance(current, str):
        error.validation_stage = stage  # type: ignore[attr-defined]


class ProductionPipeline:
    """Own the complete fail-closed production lifecycle."""

    def __init__(
        self,
        paths: ProductionLifecyclePaths,
        *,
        publish: bool,
        workers: int = 12,
    ) -> None:
        self.paths = paths
        self.publish = publish
        self.workers = workers
        self.warnings: list[str] = []
        self.timings_ms: dict[str, float] = {}

    def _private(self, name: str) -> Path:
        return self.paths.private_dir / name

    def _public(self, name: str) -> Path:
        return self.paths.public_dir / name

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError(f"production lifecycle could not read {path.name!r}") from exc
        if not isinstance(value, dict):
            raise ValidationError(f"production lifecycle JSON {path.name!r} must be an object")
        return value

    @staticmethod
    def _write_json(path: Path, document: dict[str, Any]) -> None:
        atomic_write(
            path,
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    @staticmethod
    def _append_summary(path: Path) -> None:
        destination = os.environ.get("GITHUB_STEP_SUMMARY", "")
        if not destination or not path.is_file():
            return
        try:
            text = path.read_text(encoding="utf-8")
            with Path(destination).open("a", encoding="utf-8") as handle:
                handle.write(text)
                if text and not text.endswith("\n"):
                    handle.write("\n")
        except OSError as exc:
            raise ValidationError(
                "production lifecycle could not append the Actions summary"
            ) from exc

    def _record_timing(self, name: str, started: float) -> None:
        self.timings_ms[name] = round((time.perf_counter() - started) * 1000.0, 3)

    def _prepare_dirs(self) -> None:
        # Carrier evidence is supplied by the operator before this run. Keep
        # this input while clearing reports and candidates from prior runs.
        carrier_path = self._private("carrier-qualification.json")
        carrier_input = None
        if carrier_path.is_file():
            try:
                with carrier_path.open("rb") as stream:
                    raw = stream.read(_MAX_CARRIER_INPUT_BYTES + 1)
                if len(raw) > _MAX_CARRIER_INPUT_BYTES:
                    raise ValidationError("carrier qualification input exceeds the 64 KiB limit")
                carrier_input = raw.decode("utf-8")
            except (OSError, UnicodeError) as exc:
                raise ValidationError("production lifecycle could not read carrier input") from exc
        shutil.rmtree(self.paths.private_dir, ignore_errors=True)
        shutil.rmtree(self.paths.public_dir, ignore_errors=True)
        self.paths.private_dir.mkdir(parents=True, exist_ok=True)
        self.paths.public_dir.mkdir(parents=True, exist_ok=True)
        self.paths.bin_dir.mkdir(parents=True, exist_ok=True)
        if carrier_input is not None:
            atomic_write(carrier_path, carrier_input)

    def _generate(self) -> dict[str, Any]:
        result = build_candidate(
            config_path=self.paths.config,
            subscriptions_path=self.paths.subscriptions,
            policies_path=self.paths.policies,
            env=os.environ,
        )
        atomic_write(self._private("generated.yaml"), result.yaml_text)
        self._write_json(self._private("build-report.json"), result.report)
        summary = {
            "status": "generated",
            "candidate_sha256": result.report.get("candidate_sha256"),
            "successful_subscriptions": result.report.get("successful_subscriptions", 0),
            "usable_nodes": result.report.get("usable_nodes", 0),
            "regional_group_counts": result.report.get("acl4ssr_groups", {}).get(
                "regional_group_counts", {}
            ),
            "omitted_empty_groups": result.report.get("acl4ssr_groups", {}).get(
                "omitted_empty_groups", []
            ),
        }
        self._write_json(self._private("generation-summary.json"), summary)
        return summary

    def _load_derived_state(self, project: ProjectDefinition) -> None:
        scheduler = load_scheduler_history_state(
            project=project,
            output=self._private("scheduler-history.json"),
            fingerprint_key_output=self._private("scheduler-history.key"),
            env=os.environ,
        )
        self._write_json(self._private("scheduler-history-load.json"), scheduler)
        ai_cache = load_ai_qualification_cache_state(
            project=project,
            output=self._private("ai-qualification-cache.json"),
            fingerprint_key_output=self._private("ai-qualification-cache.key"),
            env=os.environ,
        )
        self._write_json(self._private("ai-qualification-cache-load.json"), ai_cache)

    def _download_primary_mihomo(self) -> Path:
        binary = self.paths.bin_dir / "mihomo-qualification"
        result = download_pinned_mihomo(
            manifest=self.paths.mihomo_manifest,
            channel="stable",
            output=binary,
        )
        self._write_json(self.paths.work_dir / "download-qualification.json", result)
        if not binary.is_file():
            raise ValidationError("production lifecycle did not obtain the primary Mihomo binary")
        return binary

    def _qualify(self, binary: Path) -> dict[str, Any]:
        result = run_production_pipeline(
            project_paths=ProjectPaths(
                config=self.paths.config,
                subscriptions=self.paths.subscriptions,
                policies=self.paths.policies,
            ),
            qualification_paths=QualificationPaths(
                candidate=self._private("generated.yaml"),
                output=self._private("config.yaml"),
                mihomo_bin=binary,
                stage_dir=self._private("stages"),
                browsing_report=self._private("browsing-qualification-summary.json"),
                ai_report=self._private("ai-qualification-summary.json"),
                history=self._private("scheduler-history.json"),
                history_key=self._private("scheduler-history.key"),
                next_history=self._private("scheduler-history-next.json"),
                cache=self._private("ai-qualification-cache.json"),
                cache_key=self._private("ai-qualification-cache.key"),
                next_cache=self._private("ai-qualification-cache-next.json"),
                # Optional self-hosted aggregate payload; absent file keeps the
                # carrier report at not_configured.
                carrier_input=(
                    self._private("carrier-qualification.json")
                    if self._private("carrier-qualification.json").is_file()
                    else None
                ),
            ),
            outputs=ProductionPipelineOutputs(
                pre_audit=self._private("production-audit.json"),
                post_audit=self._private("post-qualification-audit.json"),
                qualification=self._private("qualification-pipeline-summary.json"),
                summary_markdown=self._private("production-summary.md"),
            ),
            build_report_path=self._private("build-report.json"),
            workers=self.workers,
        )
        self._write_json(self._private("production-pipeline.json"), result)
        self._append_summary(self._private("production-summary.md"))
        return result

    def _release_candidate_stage(self, project: ProjectDefinition, binary: Path):
        result = run_release_candidate_stage(
            project=project,
            publish=self.publish,
            primary_binary=binary,
            paths=ReleaseCandidateStagePaths(
                candidate=self._private("config.yaml"),
                qualification=self._private("qualification-pipeline-summary.json"),
                baseline=self._private("current-production.yaml"),
                baseline_report=self._private("current-production-fetch.json"),
                guard_policy=self.paths.promotion_guard,
                guard_report=self._private("promotion-guard.json"),
                guard_markdown=self._private("promotion-guard.md"),
                mihomo_manifest=self.paths.mihomo_manifest,
                mihomo_work_dir=self.paths.bin_dir / "validation",
                matrix_report=self._private("mihomo-validation-matrix.json"),
                release_report=self._private("release-publication.json"),
            ),
            env=os.environ,
        )
        self.timings_ms.update(result.timings_ms)
        if self.publish:
            self._append_summary(self._private("promotion-guard.md"))
        return result

    def _best_effort_state(self, stage: str, operation) -> dict[str, Any]:
        try:
            return operation()
        except (OSError, ValueError, ClashRelayError):
            self.warnings.append(stage)
            return {"status": "unavailable", "reason": "stage_failed"}

    def _persist_derived_state(self, project: ProjectDefinition) -> dict[str, Any]:
        if not self.publish:
            return {"status": "skipped", "reason": "dry_run"}
        cache = self._best_effort_state(
            "persist_ai_qualification_cache",
            lambda: persist_ai_qualification_cache(
                project=project,
                state=self._private("ai-qualification-cache-next.json"),
                env=os.environ,
            ),
        )
        self._write_json(self._private("ai-qualification-cache-publish.json"), cache)
        history = self._best_effort_state(
            "persist_scheduler_history",
            lambda: persist_scheduler_history(
                project=project,
                state=self._private("scheduler-history-next.json"),
                env=os.environ,
            ),
        )
        self._write_json(self._private("scheduler-history-publish.json"), history)
        return {"status": "completed", "ai_cache": cache, "scheduler_history": history}

    def _safe_source_admission_summary(self) -> dict[str, Any] | None:
        path = self._private("build-report.json")
        if not path.is_file():
            return None
        try:
            report = self._load_json(path)
        except ValidationError:
            return None
        return sanitize_source_admission_report(report)

    def _source_stage_accounting(self) -> list[dict[str, Any]]:
        """Return privacy-safe per-source stage counts for the completed candidate."""

        pre_path = self._private("production-audit.json")
        post_path = self._private("post-qualification-audit.json")
        qualification_path = self._private("qualification-pipeline-summary.json")
        if not any(path.is_file() for path in (pre_path, post_path, qualification_path)):
            return []
        pre = self._load_json(pre_path)
        post = self._load_json(post_path)
        qualification = self._load_json(qualification_path)

        def rows(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
            raw = document.get("subscriptions", [])
            if not isinstance(raw, list):
                return {}
            result: dict[str, dict[str, Any]] = {}
            for item in raw:
                if not isinstance(item, dict) or not valid_source_id(item.get("id")):
                    raise ValidationError("production source accounting has invalid source IDs")
                source_id = item["id"]
                if source_id in result:
                    raise ValidationError("production source accounting has duplicate source IDs")
                result[source_id] = item
            return result

        pre_rows = rows(pre)
        post_rows = rows(post)
        if set(pre_rows) != set(post_rows):
            raise ValidationError("production source inventories disagree on configured sources")
        provenance = safe_qualification_observability(qualification, known_source_ids=pre_rows)
        stage_rows = provenance["removed_by_stage"]
        for row in stage_rows.values():
            if (
                sum(row["by_source"].values()) != row["runtime_entries"]["removed"]
                or sum(row["added_by_source"].values()) != row["runtime_entries"]["added"]
                or sum(row["unique_by_source"].values()) != row["unique_nodes"]["removed"]
            ):
                raise ValidationError("production stage source accounting drifted")
        fully_removed = {row["source"]: row for row in provenance["sources_fully_removed"]}
        if len(fully_removed) != len(provenance["sources_fully_removed"]):
            raise ValidationError("production source accounting has duplicate removal provenance")
        result: list[dict[str, Any]] = []
        for source_id in sorted(pre_rows):
            before = pre_rows[source_id]
            after = post_rows[source_id]
            generated_runtime = int(before.get("runtime_nodes", 0) or 0)
            final_runtime = int(after.get("runtime_nodes", 0) or 0)
            by_stage = {
                stage: row["by_source"][source_id]
                for stage, row in stage_rows.items()
                if source_id in row["by_source"]
            }
            removed_runtime = sum(by_stage.values())
            added_runtime = sum(
                row["added_by_source"].get(source_id, 0) for row in stage_rows.values()
            )
            removed_unique = sum(
                row["unique_by_source"].get(source_id, 0) for row in stage_rows.values()
            )
            if generated_runtime + added_runtime - removed_runtime != final_runtime:
                raise ValidationError("production source runtime accounting drifted")
            full_removal = fully_removed.get(source_id)
            if full_removal is not None and (
                full_removal["by_stage"] != by_stage
                or full_removal["runtime_entries"] != generated_runtime
                or full_removal["unique_nodes"] != removed_unique
                or full_removal["final_unique_nodes"] != 0
                or final_runtime != 0
            ):
                raise ValidationError("production source removal provenance drifted")
            result.append(
                {
                    "id": source_id,
                    "status": str(before.get("status", "unknown")),
                    "input_nodes": int(before.get("input_nodes", 0) or 0),
                    "parsed_valid_nodes": int(before.get("parsed_valid_nodes", 0) or 0),
                    "skipped_invalid_nodes": int(before.get("skipped_invalid_nodes", 0) or 0),
                    "filtered_by_name": int(before.get("filtered_by_name", 0) or 0),
                    "filtered_over_multiplier": int(before.get("filtered_over_multiplier", 0) or 0),
                    "post_filter_nodes": int(before.get("post_multiplier_filter_nodes", 0) or 0),
                    "post_dedup_nodes": int(before.get("post_dedup_nodes", 0) or 0),
                    "generated_runtime_entries": generated_runtime,
                    "removed_runtime_entries": removed_runtime,
                    "added_runtime_entries": added_runtime,
                    "final_runtime_entries": final_runtime,
                    "removed_unique_nodes": removed_unique,
                    "removed_at_stage": (
                        full_removal["removed_at_stage"] if full_removal is not None else None
                    ),
                    "by_stage": by_stage,
                }
            )
        if (
            sum(row["removed_runtime_entries"] for row in result)
            != provenance["qualification_removed_runtime_entries"]
            or sum(row["removed_unique_nodes"] for row in result)
            != provenance["qualification_removed_unique_nodes"]
        ):
            raise ValidationError("production source removal totals drifted")
        return result

    def _render_source_quality(self) -> dict[str, Any]:
        required = (
            self._private("qualification-pipeline-summary.json"),
            self._private("config.yaml"),
            self._private("browsing-qualification-summary.json"),
        )
        if not all(path.is_file() for path in required):
            return {"status": "skipped", "reason": "evidence_unavailable"}
        report = build_source_quality_report(
            source_stage_accounting=self._source_stage_accounting(),
            qualification=self._load_json(self._private("qualification-pipeline-summary.json")),
            candidate=load_candidate(self._private("config.yaml")),
            browsing=self._load_json(self._private("browsing-qualification-summary.json")),
        )
        self._write_json(self._public("source-quality.json"), report)
        return report

    def _render_existing_proof(self, *, release: dict[str, Any] | None) -> dict[str, Any]:
        proof = render_production_proof_application(
            candidate=self._private("config.yaml"),
            audit=self._private("post-qualification-audit.json"),
            browsing=self._private("browsing-qualification-summary.json"),
            ai=self._private("ai-qualification-summary.json"),
            qualification=self._private("qualification-pipeline-summary.json"),
            release=self._private("release-publication.json") if release is not None else None,
            build_report=self._private("build-report.json"),
            promotion_guard=self._private("promotion-guard.json"),
            validated_cores_report=self._private("mihomo-validation-matrix.json"),
            publication_status="published" if self.publish else "dry-run",
            markdown=self._private("production-proof.md"),
        )
        self._write_json(self._private("production-proof.json"), proof)
        self._append_summary(self._private("production-proof.md"))
        return proof

    def _render_release_manifest(
        self,
        *,
        promotion: dict[str, Any],
        matrix: dict[str, Any],
        release: dict[str, Any] | None,
    ) -> dict[str, Any]:
        candidate_path = self._private("config.yaml")
        candidate = load_candidate(candidate_path)
        audit = self._load_json(self._private("post-qualification-audit.json"))
        qualification = self._load_json(self._private("qualification-pipeline-summary.json"))
        policy_model = load_policy_document(self.paths.policies)
        manifest = build_release_manifest(
            candidate=candidate,
            candidate_bytes=candidate_path.read_bytes(),
            audit=audit,
            qualification=qualification,
            promotion_guard=promotion,
            matrix=matrix,
            release=release,
            publication_status="published" if self.publish else "dry-run",
            policy_model_version=policy_model.model_version,
            commit_sha=os.environ.get("GITHUB_SHA") or None,
        )
        self._write_json(self._public("release-manifest.json"), manifest)
        atomic_write(
            self._public("release-manifest.md"),
            render_release_manifest_markdown(manifest),
        )
        self._append_summary(self._public("release-manifest.md"))
        return manifest

    def _post_commit_proof(self, *, release: dict[str, Any] | None) -> dict[str, Any]:
        try:
            return self._render_existing_proof(release=release)
        except (OSError, ValidationError):
            if not self.publish:
                raise
            self.warnings.append("render_production_proof")
            return {"status": "unavailable", "reason": "post_commit_observability_failed"}

    def _post_commit_manifest(
        self,
        *,
        promotion: dict[str, Any],
        matrix: dict[str, Any],
        release: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        try:
            return self._render_release_manifest(
                promotion=promotion,
                matrix=matrix,
                release=release,
            )
        except (OSError, ValidationError):
            if not self.publish:
                raise
            self.warnings.append("render_release_manifest")
            return None

    def _write_lifecycle_observability(self, progress: ReleaseProgress) -> None:
        self._write_json(
            self._private("lifecycle-observability.json"),
            {
                "timings_ms": dict(sorted(self.timings_ms.items())),
                "release_progress": progress.safe_summary(),
            },
        )

    @staticmethod
    def _safe_hostname_observability(pipeline: dict[str, Any]) -> dict[str, Any]:
        host = pipeline.get("proxy_host_resolution")
        if not isinstance(host, dict):
            return {}
        output: dict[str, Any] = {}
        sources = host.get("by_source_failure_category")
        if isinstance(sources, dict):
            clean: dict[str, dict[str, int]] = {}
            for source, categories in sources.items():
                if not isinstance(source, str) or not valid_source_id(source):
                    continue
                if not isinstance(categories, dict):
                    continue
                counts = {
                    name: value
                    for name in ("nxdomain", "no_answer")
                    if isinstance((value := categories.get(name)), int)
                    and not isinstance(value, bool)
                    and value >= 0
                }
                if counts:
                    clean[source] = counts
            output["hostname_failure_categories_by_source"] = dict(sorted(clean.items()))
        duration = host.get("probe_duration_ms")
        if (
            isinstance(duration, (int, float))
            and not isinstance(duration, bool)
            and math.isfinite(duration)
            and duration >= 0
        ):
            output["hostname_probe_duration_ms"] = duration
        count = host.get("unique_hostnames_probed")
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            output["unique_hostnames_probed"] = count
        return output

    def _preserve_ambiguous_release(self, project: ProjectDefinition) -> str:
        """Reconcile while exact private bytes remain, retaining them if unresolved."""
        candidate = self._private("config.yaml")
        baseline = self._private("current-production.yaml")
        baseline_known = baseline.with_suffix(".captured").is_file()
        if not candidate.is_file():
            return "candidate_missing"
        status = "staging_unknown"
        if baseline_known:
            try:
                result = reconcile_production_release(
                    project=project,
                    candidate=candidate,
                    previous=baseline if baseline.is_file() else None,
                    env=os.environ,
                )
                if result["status"] in {"committed", "not_committed"}:
                    return str(result["status"])
                status = str(result["status"])
            except PublicationError:
                status = "read_failed"

        recovery_root = self.paths.work_dir / "recovery"
        recovery_root.mkdir(parents=True, exist_ok=True)
        os.chmod(recovery_root, 0o700)
        content = candidate.read_bytes()
        recovery = recovery_root / f"{time.time_ns()}-{hashlib.sha256(content).hexdigest()[:12]}"
        recovery.mkdir(mode=0o700)
        for source, name in ((candidate, "candidate.yaml"), (baseline, "previous.yaml")):
            if name == "previous.yaml" and not baseline_known:
                continue
            if source.is_file():
                destination = recovery / name
                atomic_write_bytes(destination, source.read_bytes())
        self._write_json(
            recovery / "metadata.json",
            {
                "version": 1,
                "reconciliation_status": status,
                "candidate_sha256": hashlib.sha256(content).hexdigest(),
                "previous_present": baseline_known and baseline.is_file(),
                "baseline_known": baseline_known,
                "created_epoch": int(time.time()),
                "review_by_epoch": int(time.time()) + 7 * 24 * 60 * 60,
            },
        )
        return "preserved"

    def run(self) -> dict[str, Any]:
        if (
            not self.paths.config.is_file()
            or not self.paths.subscriptions.is_file()
            or not self.paths.policies.is_file()
        ):
            return {
                "status": "skipped",
                "publication_status": "not_applicable",
                "reason": "canonical_declarations_missing",
            }

        progress = ReleaseProgress(publish=self.publish)
        lifecycle_started = time.perf_counter()
        project: ProjectDefinition | None = None
        try:
            self._prepare_dirs()
            project = ProjectPaths(
                config=self.paths.config,
                subscriptions=self.paths.subscriptions,
                policies=self.paths.policies,
            ).load()
            publication_gate(project.config, "cloudflare_kv")

            started = time.perf_counter()
            try:
                generation = self._generate()
            except ValidationError as exc:
                _tag_validation_stage(exc, "generation_validation")
                raise
            self._record_timing("generation", started)
            progress.advance(ReleasePhase.PREPARED)

            started = time.perf_counter()
            try:
                self._load_derived_state(project)
            except ValidationError as exc:
                _tag_validation_stage(exc, "derived_state_load")
                raise
            self._record_timing("derived_state_load", started)

            started = time.perf_counter()
            try:
                binary = self._download_primary_mihomo()
            except ValidationError as exc:
                _tag_validation_stage(exc, "mihomo_download")
                raise
            self._record_timing("mihomo_download", started)

            started = time.perf_counter()
            try:
                pipeline = self._qualify(binary)
            except ValidationError as exc:
                _tag_validation_stage(exc, "qualification_pipeline")
                raise
            self._record_timing("qualification", started)
            progress.advance(ReleasePhase.QUALIFIED)

            started = time.perf_counter()
            source_quality = self._render_source_quality()
            self._record_timing("source_quality", started)

            try:
                release_stage = self._release_candidate_stage(project, binary)
            except ValidationError as exc:
                _tag_validation_stage(exc, "publication_validation")
                raise
            promotion = release_stage.promotion
            matrix = release_stage.matrix
            release = release_stage.release
            progress.advance(ReleasePhase.PROMOTED)
            if self.publish:
                progress.advance(ReleasePhase.PUBLISHED)

            started = time.perf_counter()
            derived_state = self._persist_derived_state(project)
            self._record_timing("derived_state_persist", started)

            proof = self._post_commit_proof(release=release)
            manifest = self._post_commit_manifest(
                promotion=promotion,
                matrix=matrix,
                release=release,
            )
            if proof.get("status") == "passed" and manifest is not None:
                progress.advance(ReleasePhase.VERIFIED)

            self._write_lifecycle_observability(progress)
            observability = publish_post_release_observability(
                project=project,
                publish=self.publish,
                private_dir=self.paths.private_dir,
                lifecycle_started=lifecycle_started,
                env=os.environ,
            )
            self.timings_ms.update(observability.timings_ms)
            self.warnings.extend(observability.warnings)
            metrics = observability.production_metrics
            scheduler_observation = observability.scheduler_observation
            slo = observability.operational_slo

            if self.warnings:
                print(
                    "::warning title=Post-release state/observability::Production release is valid, "
                    "but one or more optional post-release stages failed: "
                    + ", ".join(sorted(self.warnings))
                )

            release_id = (
                manifest.get("release_id")
                if manifest is not None
                else release.get("release_id")
                if release is not None
                else None
            )
            config_sha256 = (
                manifest.get("config_sha256")
                if manifest is not None
                else release.get("sha256")
                if release is not None
                else None
            )
            return {
                "status": "passed",
                "publication_status": "published" if self.publish else "dry-run",
                "generation": generation.get("status"),
                "production_pipeline": pipeline.get("production_pipeline", {}).get("status"),
                "promotion_guard": promotion.get("status"),
                "mihomo_matrix": matrix.get("status"),
                "release_status": release.get("status") if release is not None else "dry-run",
                "release_phase": progress.phase,
                "release_id": release_id,
                "config_sha256": config_sha256,
                "proof_status": proof.get("status", "unavailable"),
                "manifest_status": "passed" if manifest is not None else "unavailable",
                "derived_state": derived_state.get("status"),
                "production_metrics": metrics.get("status"),
                "source_quality": source_quality.get("status"),
                "scheduler_observation": scheduler_observation.get("status"),
                "operational_slo": slo.get("status"),
                "source_stage_accounting": self._source_stage_accounting(),
                "regional_group_counts": generation.get("regional_group_counts", {}),
                "omitted_empty_groups": generation.get("omitted_empty_groups", []),
                "endpoint_qualification": pipeline.get("endpoint_qualification"),
                **self._safe_hostname_observability(pipeline),
                "accelerated_health_check_groups": pipeline.get(
                    "accelerated_health_check_groups", 0
                ),
                "warnings": sorted(self.warnings),
            }
        except Exception as exc:
            if self.publish and project is not None and isinstance(exc, CommitUnknownError):
                candidate_path = self._private("config.yaml")
                baseline_path = self._private("current-production.yaml")
                baseline_known = baseline_path.with_suffix(".captured").is_file()
                try:
                    if candidate_path.is_file():
                        candidate_id = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
                        exc.candidate_release_id = candidate_id  # type: ignore[attr-defined]
                    if baseline_known:
                        previous_id = (
                            hashlib.sha256(baseline_path.read_bytes()).hexdigest()
                            if baseline_path.is_file()
                            else None
                        )
                        exc.previous_release_id = previous_id  # type: ignore[attr-defined]
                except OSError:
                    pass
                try:
                    exc.recovery_status = self._preserve_ambiguous_release(project)  # type: ignore[attr-defined]
                except OSError:
                    exc.recovery_status = "preservation_failed"  # type: ignore[attr-defined]
            source_admission = self._safe_source_admission_summary()
            if source_admission is not None:
                exc.source_admission_report = source_admission  # type: ignore[attr-defined]
            if project is not None:
                failure_observability = record_failure_observability(
                    project=project,
                    publish=self.publish,
                    private_dir=self.paths.private_dir,
                    lifecycle_started=lifecycle_started,
                    error=exc,
                    env=os.environ,
                )
                self.warnings.extend(failure_observability.warnings)
            raise
        finally:
            shutil.rmtree(self.paths.private_dir, ignore_errors=True)
