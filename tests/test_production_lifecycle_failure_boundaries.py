from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import clash_relay.production_lifecycle as lifecycle
from clash_relay.errors import ValidationError
from clash_relay.operational_slo import ProductionOutcome
from clash_relay.production_lifecycle import ProductionLifecyclePaths, ProductionPipeline
from clash_relay.qualification_reliability import (
    QualificationFailureCategory,
    QualificationStageRejected,
)


def _pipeline(tmp_path: Path, *, publish: bool = False) -> ProductionPipeline:
    return ProductionPipeline(ProductionLifecyclePaths.canonical(tmp_path), publish=publish)


def _write_canonical_declarations(paths: ProductionLifecyclePaths) -> None:
    for declaration in (paths.config, paths.subscriptions, paths.policies):
        declaration.parent.mkdir(parents=True, exist_ok=True)
        declaration.write_text("fixture: true\n", encoding="utf-8")


def test_private_workdir_is_removed_when_lifecycle_fails_after_preparation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _pipeline(tmp_path)
    _write_canonical_declarations(pipeline.paths)

    def fail_load(_self: Any) -> Any:
        assert pipeline.paths.private_dir.is_dir()
        (pipeline.paths.private_dir / "sensitive-candidate.yaml").write_text(
            "private: true\n", encoding="utf-8"
        )
        raise ValidationError("fixture project load failure")

    monkeypatch.setattr(lifecycle.ProjectPaths, "load", fail_load)

    with pytest.raises(ValidationError, match="fixture project load failure"):
        pipeline.run()

    assert not pipeline.paths.private_dir.exists()


def test_best_effort_state_never_swallows_programming_errors(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path, publish=True)

    def fail() -> dict[str, Any]:
        raise RuntimeError("programming defect")

    with pytest.raises(RuntimeError, match="programming defect"):
        pipeline._best_effort_state("optional_state", fail)

    assert pipeline.warnings == []


def test_dry_run_post_commit_manifest_failure_remains_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _pipeline(tmp_path, publish=False)

    def fail_manifest(**_kwargs: Any) -> dict[str, Any]:
        raise ValidationError("fixture manifest failure")

    monkeypatch.setattr(pipeline, "_render_release_manifest", fail_manifest)

    with pytest.raises(ValidationError, match="fixture manifest failure"):
        pipeline._post_commit_manifest(promotion={}, matrix={}, release=None)

    assert pipeline.warnings == []


def test_typed_qualification_failure_records_rejected_outcome_and_retry_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _pipeline(tmp_path, publish=False)
    _write_canonical_declarations(pipeline.paths)
    project = SimpleNamespace(config={})
    binary = tmp_path / "fixture-mihomo"
    binary.write_bytes(b"fixture")

    monkeypatch.setattr(lifecycle.ProjectPaths, "load", lambda _self: project)
    monkeypatch.setattr(lifecycle, "publication_gate", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "_generate", lambda: {"status": "generated"})
    monkeypatch.setattr(pipeline, "_load_derived_state", lambda _project: None)
    monkeypatch.setattr(pipeline, "_download_primary_mihomo", lambda: binary)

    rejection = QualificationStageRejected(
        stage="browsing",
        category=QualificationFailureCategory.TRANSIENT,
        retryable=True,
    )
    wrapped = ValidationError("aggregate qualification failure")
    wrapped.__cause__ = rejection

    def fail_qualification(_binary: Path) -> dict[str, Any]:
        raise wrapped

    monkeypatch.setattr(pipeline, "_qualify", fail_qualification)

    recorded: dict[str, Any] = {}

    def record_slo(**kwargs: Any) -> dict[str, Any]:
        recorded.update(kwargs)
        return {"status": "skipped", "reason": "test"}

    monkeypatch.setattr(pipeline, "_record_operational_slo", record_slo)

    with pytest.raises(ValidationError, match="aggregate qualification failure"):
        pipeline.run()

    assert recorded["outcome"] is ProductionOutcome.QUALIFICATION_REJECTED
    assert recorded["failure_category"] == QualificationFailureCategory.TRANSIENT.value
    assert recorded["failure_retry_attempted"] is True
    assert not pipeline.paths.private_dir.exists()
