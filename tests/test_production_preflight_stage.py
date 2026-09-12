from __future__ import annotations

from typing import Any, cast

import pytest

from clash_relay.errors import ValidationError
from clash_relay.production_release_stage import (
    ReleaseCandidateStagePaths,
    run_release_candidate_stage,
)


def _paths(tmp_path) -> ReleaseCandidateStagePaths:
    return ReleaseCandidateStagePaths(
        candidate=tmp_path / "config.yaml",
        qualification=tmp_path / "qualification.json",
        baseline=tmp_path / "baseline.yaml",
        baseline_report=tmp_path / "baseline.json",
        guard_policy=tmp_path / "promotion-guard.yaml",
        guard_report=tmp_path / "promotion-guard.json",
        guard_markdown=tmp_path / "promotion-guard.md",
        mihomo_manifest=tmp_path / "mihomo-versions.json",
        mihomo_work_dir=tmp_path / "mihomo-validation",
        matrix_report=tmp_path / "mihomo-matrix.json",
        release_report=tmp_path / "release.json",
    )


def _project() -> Any:
    return cast(Any, object())


def test_production_preflight_reads_live_baseline_and_runs_all_gates_without_publish(
    monkeypatch, tmp_path
) -> None:
    calls: list[str] = []
    paths = _paths(tmp_path)

    def fetch(**kwargs):
        calls.append("baseline")
        assert kwargs["allow_missing"] is True
        assert kwargs["output"] == paths.baseline
        return {"status": "fetched", "sha256": "baseline"}

    def guard(**kwargs):
        calls.append("guard")
        assert kwargs["baseline_path"] == paths.baseline
        return {"status": "passed"}

    def matrix(**kwargs):
        calls.append("matrix")
        return {"status": "passed", "validated_cores": ["v1.19.29", "v1.19.30"]}

    def publish(**kwargs):
        calls.append("publish")
        raise AssertionError("production preflight must never publish")

    monkeypatch.setattr(
        "clash_relay.production_release_stage.fetch_current_production_config", fetch
    )
    monkeypatch.setattr("clash_relay.production_release_stage.run_promotion_guard", guard)
    monkeypatch.setattr("clash_relay.production_release_stage.validate_mihomo_matrix", matrix)
    monkeypatch.setattr("clash_relay.production_release_stage.publish_production_release", publish)

    result = run_release_candidate_stage(
        project=_project(),
        publish=False,
        preflight=True,
        primary_binary=tmp_path / "mihomo",
        paths=paths,
        env={},
    )

    assert calls == ["baseline", "guard", "matrix"]
    assert result.promotion["status"] == "passed"
    assert result.matrix["status"] == "passed"
    assert result.release is None
    assert not paths.release_report.exists()


def test_publish_and_production_preflight_cannot_be_enabled_together(tmp_path) -> None:
    with pytest.raises(ValidationError, match="mutually exclusive"):
        run_release_candidate_stage(
            project=_project(),
            publish=True,
            preflight=True,
            primary_binary=tmp_path / "mihomo",
            paths=_paths(tmp_path),
            env={},
        )
