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


def test_release_stage_orders_guard_matrix_then_publication(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    paths = _paths(tmp_path)

    def fetch(**kwargs):
        calls.append("baseline")
        assert kwargs["output"] == paths.baseline
        return {"status": "fetched"}

    def guard(**kwargs):
        calls.append("guard")
        assert kwargs["qualification_path"] == paths.qualification
        return {"status": "passed"}

    def matrix(**kwargs):
        calls.append("matrix")
        return {"status": "passed", "validated_cores": ["v1", "v2"]}

    def publish(**kwargs):
        calls.append("publish")
        return {"status": "published", "sha256": "abc"}

    monkeypatch.setattr(
        "clash_relay.production_release_stage.fetch_current_production_config", fetch
    )
    monkeypatch.setattr("clash_relay.production_release_stage.run_promotion_guard", guard)
    monkeypatch.setattr("clash_relay.production_release_stage.validate_mihomo_matrix", matrix)
    monkeypatch.setattr("clash_relay.production_release_stage.publish_production_release", publish)

    result = run_release_candidate_stage(
        project=_project(),
        publish=True,
        primary_binary=tmp_path / "mihomo",
        paths=paths,
        env={},
    )

    assert calls == ["baseline", "guard", "matrix", "publish"]
    assert result.promotion["status"] == "passed"
    assert result.matrix["status"] == "passed"
    assert result.release == {"status": "published", "sha256": "abc"}
    assert set(result.timings_ms) == {"promotion_guard", "mihomo_matrix", "publication"}


def test_guard_failure_prevents_matrix_and_publication(monkeypatch, tmp_path) -> None:
    calls: list[str] = []

    def fetch(**kwargs):
        calls.append("baseline")
        return {"status": "fetched"}

    def guard(**kwargs):
        calls.append("guard")
        raise ValidationError("promotion blocked")

    def unexpected(**kwargs):
        calls.append("unexpected")
        return {"status": "passed"}

    monkeypatch.setattr(
        "clash_relay.production_release_stage.fetch_current_production_config", fetch
    )
    monkeypatch.setattr("clash_relay.production_release_stage.run_promotion_guard", guard)
    monkeypatch.setattr("clash_relay.production_release_stage.validate_mihomo_matrix", unexpected)
    monkeypatch.setattr(
        "clash_relay.production_release_stage.publish_production_release", unexpected
    )

    with pytest.raises(ValidationError, match="promotion blocked"):
        run_release_candidate_stage(
            project=_project(),
            publish=True,
            primary_binary=tmp_path / "mihomo",
            paths=_paths(tmp_path),
            env={},
        )

    assert calls == ["baseline", "guard"]


def test_matrix_failure_prevents_publication(monkeypatch, tmp_path) -> None:
    calls: list[str] = []

    def fetch(**kwargs):
        calls.append("baseline")
        return {"status": "fetched"}

    def guard(**kwargs):
        calls.append("guard")
        return {"status": "passed"}

    def matrix(**kwargs):
        calls.append("matrix")
        raise ValidationError("core rejected")

    def publish(**kwargs):
        calls.append("publish")
        return {"status": "published"}

    monkeypatch.setattr(
        "clash_relay.production_release_stage.fetch_current_production_config", fetch
    )
    monkeypatch.setattr("clash_relay.production_release_stage.run_promotion_guard", guard)
    monkeypatch.setattr("clash_relay.production_release_stage.validate_mihomo_matrix", matrix)
    monkeypatch.setattr("clash_relay.production_release_stage.publish_production_release", publish)

    with pytest.raises(ValidationError, match="core rejected"):
        run_release_candidate_stage(
            project=_project(),
            publish=True,
            primary_binary=tmp_path / "mihomo",
            paths=_paths(tmp_path),
            env={},
        )

    assert calls == ["baseline", "guard", "matrix"]


def test_dry_run_skips_production_state_but_keeps_real_core_matrix(monkeypatch, tmp_path) -> None:
    calls: list[str] = []

    def unexpected(**kwargs):
        calls.append("unexpected")
        return {"status": "unexpected"}

    def matrix(**kwargs):
        calls.append("matrix")
        return {"status": "passed", "validated_cores": ["v1", "v2"]}

    monkeypatch.setattr(
        "clash_relay.production_release_stage.fetch_current_production_config", unexpected
    )
    monkeypatch.setattr("clash_relay.production_release_stage.run_promotion_guard", unexpected)
    monkeypatch.setattr("clash_relay.production_release_stage.validate_mihomo_matrix", matrix)
    monkeypatch.setattr(
        "clash_relay.production_release_stage.publish_production_release", unexpected
    )

    result = run_release_candidate_stage(
        project=_project(),
        publish=False,
        primary_binary=tmp_path / "mihomo",
        paths=_paths(tmp_path),
        env={},
    )

    assert calls == ["matrix"]
    assert result.promotion == {"status": "skipped", "reason": "dry_run"}
    assert result.release is None
