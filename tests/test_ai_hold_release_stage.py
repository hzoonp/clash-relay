"""Production release-stage fault injection for OpenAI probe-environment holds.

The real promotion guard consumes a real qualification summary; a systemic
OpenAI blackout must HOLD the release before Mihomo matrix validation and
before ``publish_production_release`` can run, while a cache-backed (LKG)
verdict continues to publication. The previous verified release bytes are
never modified on a hold.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from clash_relay.errors import ValidationError
from clash_relay.production_release_stage import (
    ReleaseCandidateStagePaths,
    run_release_candidate_stage,
)
from test_ai_systemic_qualification import _generated_candidate


def _paths(tmp_path: Path) -> ReleaseCandidateStagePaths:
    return ReleaseCandidateStagePaths(
        candidate=tmp_path / "config.yaml",
        qualification=tmp_path / "qualification.json",
        baseline=tmp_path / "baseline.yaml",
        baseline_report=tmp_path / "baseline.json",
        guard_policy=Path(__file__).resolve().parents[1] / "promotion-guard.yaml",
        guard_report=tmp_path / "promotion-guard.json",
        guard_markdown=tmp_path / "promotion-guard.md",
        mihomo_manifest=tmp_path / "mihomo-versions.json",
        mihomo_work_dir=tmp_path / "mihomo-validation",
        matrix_report=tmp_path / "mihomo-matrix.json",
        release_report=tmp_path / "release.json",
    )


def _candidate_file(repo_root: Path, tmp_path: Path) -> Path:
    built = _generated_candidate(repo_root, tmp_path)
    candidate = tmp_path / "config.yaml"
    candidate.write_text(built.yaml_text, encoding="utf-8")
    return candidate


def _qualification_document(
    *,
    openai_qualified: int,
    openai_regions: int,
    openai_evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": "qualified",
        "ai": {
            "service_evidence": {"openai": openai_evidence},
            "services": {
                "openai": {
                    "qualified_candidates": openai_qualified,
                    "qualified_regions": openai_regions,
                },
                "claude": {"qualified_candidates": 6, "qualified_regions": 1},
                "gemini": {"qualified_candidates": 115, "qualified_regions": 3},
            },
        },
    }


def _write_previous_release(path: Path, candidate_bytes: bytes) -> None:
    path.write_bytes(candidate_bytes)


def test_probe_environment_hold_blocks_before_publication_and_preserves_previous_release(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_file(repo_root, tmp_path)
    previous_release_bytes = candidate.read_bytes()
    paths = _paths(tmp_path)
    _write_previous_release(paths.baseline, previous_release_bytes)

    paths.qualification.write_text(
        json.dumps(
            _qualification_document(
                openai_qualified=0,
                openai_regions=0,
                openai_evidence={
                    "evidence_status": "inconclusive",
                    "systemic_failure_detected": True,
                    "lkg_fresh": False,
                    "evidence_source": "none",
                },
            )
        ),
        encoding="utf-8",
    )

    calls: list[str] = []

    def fetch(**kwargs):
        calls.append("fetch_previous_release")
        _write_previous_release(kwargs["output"], previous_release_bytes)
        return {"status": "fetched"}

    def fail_matrix(**kwargs):
        calls.append("matrix")
        raise AssertionError("matrix must not run after a probe_environment_hold")

    def fail_publish(**kwargs):
        calls.append("publish")
        raise AssertionError("publication must not run after a probe_environment_hold")

    monkeypatch.setattr(
        "clash_relay.production_release_stage.fetch_current_production_config", fetch
    )
    monkeypatch.setattr("clash_relay.production_release_stage.validate_mihomo_matrix", fail_matrix)
    monkeypatch.setattr(
        "clash_relay.production_release_stage.publish_production_release", fail_publish
    )

    with pytest.raises(ValidationError, match="promotion guard blocked") as caught:
        run_release_candidate_stage(
            project=_project(repo_root),
            publish=True,
            primary_binary=tmp_path / "mihomo",
            paths=paths,
            env={},
        )

    report = getattr(caught.value, "promotion_guard_report", None)
    assert report is not None
    assert report["reason"] == "probe_environment_hold"
    assert report["probe_environment"]["held_services"] == ["openai"]
    # The previous verified release is preserved byte-for-byte.
    assert paths.baseline.read_bytes() == previous_release_bytes
    # HOLD happened before matrix validation and publication.
    assert calls == ["fetch_previous_release"]


def test_systemic_with_fresh_lkg_continues_to_publication(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_file(repo_root, tmp_path)
    previous_release_bytes = candidate.read_bytes()
    paths = _paths(tmp_path)
    _write_previous_release(paths.baseline, previous_release_bytes)

    paths.qualification.write_text(
        json.dumps(
            _qualification_document(
                openai_qualified=1,
                openai_regions=1,
                openai_evidence={
                    "evidence_status": "inconclusive",
                    "systemic_failure_detected": True,
                    "lkg_fresh": True,
                    "evidence_source": "cache",
                },
            )
        ),
        encoding="utf-8",
    )

    calls: list[str] = []

    def fetch(**kwargs):
        calls.append("fetch_previous_release")
        _write_previous_release(kwargs["output"], previous_release_bytes)
        return {"status": "fetched"}

    def matrix(**kwargs):
        calls.append("matrix")
        return {"status": "passed", "validated_cores": ["v1.19.29", "v1.19.30"]}

    def publish(**kwargs):
        calls.append("publish")
        return {"status": "published", "sha256": "lkg-sha"}

    monkeypatch.setattr(
        "clash_relay.production_release_stage.fetch_current_production_config", fetch
    )
    monkeypatch.setattr("clash_relay.production_release_stage.validate_mihomo_matrix", matrix)
    monkeypatch.setattr("clash_relay.production_release_stage.publish_production_release", publish)

    result = run_release_candidate_stage(
        project=_project(repo_root),
        publish=True,
        primary_binary=tmp_path / "mihomo",
        paths=paths,
        env={},
    )

    assert calls == ["fetch_previous_release", "matrix", "publish"]
    assert result.promotion["status"] == "passed"
    assert result.promotion["probe_environment"]["cache_backed_services"] == ["openai"]
    assert result.release == {"status": "published", "sha256": "lkg-sha"}


def test_confirmed_openai_collapse_blocks_as_degraded(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_file(repo_root, tmp_path)
    previous_release_bytes = candidate.read_bytes()
    paths = _paths(tmp_path)
    _write_previous_release(paths.baseline, previous_release_bytes)

    paths.qualification.write_text(
        json.dumps(
            _qualification_document(
                openai_qualified=0,
                openai_regions=0,
                openai_evidence={
                    "evidence_status": "failed",
                    "systemic_failure_detected": False,
                    "lkg_fresh": False,
                    "evidence_source": "live",
                },
            )
        ),
        encoding="utf-8",
    )

    calls: list[str] = []

    def fetch(**kwargs):
        calls.append("fetch_previous_release")
        _write_previous_release(kwargs["output"], previous_release_bytes)
        return {"status": "fetched"}

    def fail_after_guard(**kwargs):
        calls.append("unexpected")
        raise AssertionError("nothing may run after a confirmed collapse block")

    monkeypatch.setattr(
        "clash_relay.production_release_stage.fetch_current_production_config", fetch
    )
    monkeypatch.setattr(
        "clash_relay.production_release_stage.validate_mihomo_matrix", fail_after_guard
    )
    monkeypatch.setattr(
        "clash_relay.production_release_stage.publish_production_release", fail_after_guard
    )

    with pytest.raises(ValidationError, match="promotion guard blocked") as caught:
        run_release_candidate_stage(
            project=_project(repo_root),
            publish=True,
            primary_binary=tmp_path / "mihomo",
            paths=paths,
            env={},
        )

    report = getattr(caught.value, "promotion_guard_report", None)
    assert report is not None
    assert report["reason"] == "degraded"
    assert "minimum_qualified_nodes:openai" in report["violations"]
    assert calls == ["fetch_previous_release"]


def _project(repo_root: Path) -> Any:
    from clash_relay.config_loader import load_project

    return load_project(
        config_path=repo_root / "config.yaml",
        subscriptions_path=repo_root / "subscriptions.yaml",
        policies_path=repo_root / "policies.yaml",
    )
