"""Single application-layer owner for the production release lifecycle.

GitHub Actions is intentionally a thin adapter. This module owns production
execution order and calls package application services directly. The only
remaining process boundaries are true external programs such as Mihomo.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .builder import build_candidate
from .config_loader import ProjectDefinition
from .errors import ClashRelayError, ValidationError
from .mihomo import load_candidate
from .mihomo_download import download_pinned_mihomo
from .mihomo_matrix_application import validate_mihomo_matrix
from .operational_slo import (
    ProductionOutcome,
    build_slo_attempt,
    qualification_failure_category,
    qualification_retry_attempted,
)
from .policy_document import load_policy_document
from .production_application import (
    fetch_current_production_config,
    load_ai_qualification_cache_state,
    load_scheduler_history_state,
    persist_ai_qualification_cache,
    persist_production_metrics,
    persist_scheduler_history,
    publish_production_release,
    render_production_proof_application,
    run_promotion_guard,
)
from .production_pipeline import (
    ProductionPipelineOutputs,
    ProjectPaths,
    QualificationPaths,
    run_production_pipeline,
)
from .publication import publication_gate
from .release_manifest import build_release_manifest, render_release_manifest_markdown
from .release_reliability import ReleasePhase, ReleaseProgress
from .slo_application import persist_operational_slo
from .util import atomic_write


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


def resolve_publication_mode(
    *,
    explicit_publish: bool | None = None,
    event_name: str | None = None,
    manual_publish: str | bool | None = None,
) -> bool:
    """Resolve push/schedule/manual publication semantics without workflow shell logic."""

    if explicit_publish is not None:
        return explicit_publish
    if not event_name:
        return False
    if event_name in {"push", "schedule"}:
        return True
    if event_name == "workflow_dispatch":
        if isinstance(manual_publish, bool):
            return manual_publish
        return str(manual_publish or "").strip().lower() == "true"
    raise ValidationError(f"unsupported production publication event {event_name!r}")


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
        shutil.rmtree(self.paths.private_dir, ignore_errors=True)
        shutil.rmtree(self.paths.public_dir, ignore_errors=True)
        self.paths.private_dir.mkdir(parents=True, exist_ok=True)
        self.paths.public_dir.mkdir(parents=True, exist_ok=True)
        self.paths.bin_dir.mkdir(parents=True, exist_ok=True)

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

    def _promotion_guard(self, project: ProjectDefinition) -> dict[str, Any]:
        if not self.publish:
            return {"status": "skipped", "reason": "dry_run"}
        baseline = fetch_current_production_config(
            project=project,
            output=self._private("current-production.yaml"),
            allow_missing=True,
            env=os.environ,
        )
        self._write_json(self._private("current-production-fetch.json"), baseline)
        report = run_promotion_guard(
            project=project,
            candidate_path=self._private("config.yaml"),
            baseline_path=self._private("current-production.yaml"),
            guard_path=self.paths.promotion_guard,
            report_path=self._private("promotion-guard.json"),
            markdown_path=self._private("promotion-guard.md"),
        )
        self._append_summary(self._private("promotion-guard.md"))
        return report

    def _validate_matrix(self, binary: Path) -> dict[str, Any]:
        result = validate_mihomo_matrix(
            candidate=self._private("config.yaml"),
            manifest=self.paths.mihomo_manifest,
            channel="stable",
            work_dir=self.paths.bin_dir / "validation",
            reuse_primary_bin=binary,
        )
        self._write_json(self._private("mihomo-validation-matrix.json"), result)
        return result

    def _publish_release(self, project: ProjectDefinition) -> dict[str, Any] | None:
        if not self.publish:
            return None
        result = publish_production_release(
            project=project,
            candidate_path=self._private("config.yaml"),
            env=os.environ,
        )
        self._write_json(self._private("release-publication.json"), result)
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

    def _persist_production_metrics(self, project: ProjectDefinition) -> dict[str, Any]:
        if not self.publish:
            return {"status": "skipped", "reason": "dry_run"}
        result = self._best_effort_state(
            "persist_production_metrics",
            lambda: persist_production_metrics(
                project=project,
                private_dir=self.paths.private_dir,
                env=os.environ,
            ),
        )
        self._write_json(self._private("production-metrics-publish.json"), result)
        return result

    def _candidate_slo_identity(self) -> tuple[str | None, int | None]:
        for path in (self._private("config.yaml"), self._private("generated.yaml")):
            try:
                content = path.read_bytes()
            except OSError:
                continue
            if content:
                return hashlib.sha256(content).hexdigest(), len(content)
        return None, None

    def _qualification_retry_state(self) -> tuple[bool, bool]:
        path = self._private("qualification-pipeline-summary.json")
        if not path.is_file():
            return False, False
        try:
            summary = self._load_json(path)
        except ValidationError:
            return False, False
        browsing = summary.get("browsing")
        if not isinstance(browsing, dict):
            return False, False
        attempts = browsing.get("stage_attempts", 1)
        retry_attempted = (
            isinstance(attempts, int) and not isinstance(attempts, bool) and attempts > 1
        )
        return retry_attempted, browsing.get("recovered_by_retry") is True

    def _promotion_slo_state(self) -> tuple[bool, bool]:
        path = self._private("promotion-guard.json")
        if not path.is_file():
            return False, False
        try:
            report = self._load_json(path)
        except ValidationError:
            return False, False
        status = report.get("status")
        return status in {"passed", "blocked"}, status == "blocked"

    def _record_operational_slo(
        self,
        *,
        project: ProjectDefinition,
        outcome: ProductionOutcome,
        lifecycle_started: float,
        failure_category: str | None = None,
        failure_retry_attempted: bool = False,
    ) -> dict[str, Any]:
        if not self.publish:
            return {"status": "skipped", "reason": "dry_run"}
        candidate_sha256, candidate_bytes = self._candidate_slo_identity()
        retry_attempted, retry_recovered = self._qualification_retry_state()
        retry_attempted = retry_attempted or failure_retry_attempted
        guard_checked, guard_blocked = self._promotion_slo_state()
        try:
            attempt = build_slo_attempt(
                outcome=outcome,
                duration_ms=(time.perf_counter() - lifecycle_started) * 1000.0,
                candidate_sha256=candidate_sha256,
                candidate_bytes=candidate_bytes,
                qualification_failure_category=failure_category,
                retry_attempted=retry_attempted,
                retry_recovered=retry_recovered,
                promotion_guard_checked=guard_checked,
                promotion_guard_blocked=guard_blocked,
            )
            result = persist_operational_slo(project=project, attempt=attempt, env=os.environ)
            self._write_json(self._private("operational-slo-publish.json"), result)
            return result
        except (OSError, ValueError, ClashRelayError):
            self.warnings.append("persist_operational_slo")
            return {"status": "unavailable", "reason": "stage_failed"}

    def _render_existing_proof(self, *, release: dict[str, Any] | None) -> dict[str, Any]:
        proof = render_production_proof_application(
            candidate=self._private("config.yaml"),
            audit=self._private("post-qualification-audit.json"),
            browsing=self._private("browsing-qualification-summary.json"),
            ai=self._private("ai-qualification-summary.json"),
            qualification=self._private("qualification-pipeline-summary.json"),
            release=self._private("release-publication.json") if release is not None else None,
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

        self._prepare_dirs()
        progress = ReleaseProgress(publish=self.publish)
        lifecycle_started = time.perf_counter()
        project: ProjectDefinition | None = None
        try:
            project = ProjectPaths(
                config=self.paths.config,
                subscriptions=self.paths.subscriptions,
                policies=self.paths.policies,
            ).load()
            publication_gate(project.config, "cloudflare_kv")

            started = time.perf_counter()
            generation = self._generate()
            self._record_timing("generation", started)
            progress.advance(ReleasePhase.PREPARED)

            started = time.perf_counter()
            self._load_derived_state(project)
            self._record_timing("derived_state_load", started)

            started = time.perf_counter()
            binary = self._download_primary_mihomo()
            self._record_timing("mihomo_download", started)

            started = time.perf_counter()
            pipeline = self._qualify(binary)
            self._record_timing("qualification", started)
            progress.advance(ReleasePhase.QUALIFIED)

            started = time.perf_counter()
            promotion = self._promotion_guard(project)
            self._record_timing("promotion_guard", started)

            started = time.perf_counter()
            matrix = self._validate_matrix(binary)
            self._record_timing("mihomo_matrix", started)
            progress.advance(ReleasePhase.PROMOTED)

            started = time.perf_counter()
            release = self._publish_release(project)
            self._record_timing("publication", started)
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
            started = time.perf_counter()
            metrics = self._persist_production_metrics(project)
            self._record_timing("production_metrics", started)
            slo = self._record_operational_slo(
                project=project,
                outcome=ProductionOutcome.PASSED,
                lifecycle_started=lifecycle_started,
            )

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
                "operational_slo": slo.get("status"),
                "warnings": sorted(self.warnings),
            }
        except Exception as exc:
            if project is not None:
                category = qualification_failure_category(exc)
                guard_checked, guard_blocked = self._promotion_slo_state()
                outcome = (
                    ProductionOutcome.QUALIFICATION_REJECTED
                    if category is not None
                    else ProductionOutcome.PROMOTION_BLOCKED
                    if guard_checked and guard_blocked
                    else ProductionOutcome.FAILED
                )
                self._record_operational_slo(
                    project=project,
                    outcome=outcome,
                    lifecycle_started=lifecycle_started,
                    failure_category=category,
                    failure_retry_attempted=qualification_retry_attempted(exc),
                )
            raise
        finally:
            shutil.rmtree(self.paths.private_dir, ignore_errors=True)
