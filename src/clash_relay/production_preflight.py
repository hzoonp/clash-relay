"""Read-only production-relative lifecycle built on the canonical pipeline."""

from __future__ import annotations

import os
from typing import Any

from .config_loader import ProjectDefinition
from .mihomo import load_candidate
from .policy_document import load_policy_document
from .production_application import render_production_proof_application
from .production_lifecycle import ProductionLifecyclePaths, ProductionPipeline
from .production_proof import render_production_proof_markdown
from .production_release_stage import ReleaseCandidateStagePaths, run_release_candidate_stage
from .release_manifest import build_release_manifest, render_release_manifest_markdown
from .util import atomic_write


class ProductionPreflightPipeline(ProductionPipeline):
    """Run production-relative gates while preserving a zero-write boundary."""

    def __init__(
        self,
        paths: ProductionLifecyclePaths,
        *,
        workers: int = 12,
    ) -> None:
        super().__init__(paths, publish=False, workers=workers)

    def _release_candidate_stage(self, project: ProjectDefinition, binary):
        result = run_release_candidate_stage(
            project=project,
            publish=False,
            preflight=True,
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
        self._append_summary(self._private("promotion-guard.md"))
        return result

    def _render_existing_proof(self, *, release: dict[str, Any] | None) -> dict[str, Any]:
        if release is not None:
            raise AssertionError("production preflight must never have release metadata")
        proof = render_production_proof_application(
            candidate=self._private("config.yaml"),
            audit=self._private("post-qualification-audit.json"),
            browsing=self._private("browsing-qualification-summary.json"),
            ai=self._private("ai-qualification-summary.json"),
            qualification=self._private("qualification-pipeline-summary.json"),
            release=None,
            validated_cores_report=self._private("mihomo-validation-matrix.json"),
            publication_status="dry-run",
            markdown=None,
        )
        proof["publication"] = "preflight"
        self._write_json(self._private("production-proof.json"), proof)
        markdown = self._private("production-proof.md")
        atomic_write(markdown, render_production_proof_markdown(proof))
        self._append_summary(markdown)
        return proof

    def _render_release_manifest(
        self,
        *,
        promotion: dict[str, Any],
        matrix: dict[str, Any],
        release: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if release is not None:
            raise AssertionError("production preflight must never have release metadata")
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
            release=None,
            publication_status="dry-run",
            policy_model_version=policy_model.model_version,
            commit_sha=os.environ.get("GITHUB_SHA") or None,
        )
        manifest["publication_status"] = "preflight"
        self._write_json(self._public("release-manifest.json"), manifest)
        markdown = self._public("release-manifest.md")
        atomic_write(markdown, render_release_manifest_markdown(manifest))
        self._append_summary(markdown)
        return manifest

    def run(self) -> dict[str, Any]:
        result = super().run()
        if result.get("status") == "passed":
            result["publication_status"] = "preflight"
            result["release_status"] = "preflight"
        return result
