from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import clash_relay.production_lifecycle as lifecycle
from clash_relay.production_lifecycle import ProductionLifecyclePaths, ProductionPipeline


def test_full_dry_run_never_invokes_external_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = ProductionLifecyclePaths.canonical(tmp_path)
    for declaration in (paths.config, paths.subscriptions, paths.policies):
        declaration.write_text("fixture: true\n", encoding="utf-8")

    project = SimpleNamespace(config={})
    binary = tmp_path / "fixture-mihomo"
    binary.write_bytes(b"fixture")
    pipeline = ProductionPipeline(paths, publish=False)

    monkeypatch.setattr(lifecycle.ProjectPaths, "load", lambda _self: project)
    monkeypatch.setattr(lifecycle, "publication_gate", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "_generate", lambda: {"status": "generated"})
    monkeypatch.setattr(pipeline, "_load_derived_state", lambda _project: None)
    monkeypatch.setattr(pipeline, "_download_primary_mihomo", lambda: binary)
    monkeypatch.setattr(
        pipeline,
        "_qualify",
        lambda _binary: {"production_pipeline": {"status": "passed"}},
    )
    monkeypatch.setattr(
        pipeline,
        "_release_candidate_stage",
        lambda _project, _binary: SimpleNamespace(
            promotion={"status": "passed"},
            matrix={"status": "passed"},
            release=None,
            timings_ms={},
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "_post_commit_proof",
        lambda *, release: {"status": "passed"},
    )
    monkeypatch.setattr(
        pipeline,
        "_post_commit_manifest",
        lambda **_kwargs: {
            "release_id": "dry-run-fixture",
            "config_sha256": "0" * 64,
        },
    )
    monkeypatch.setattr(pipeline, "_write_lifecycle_observability", lambda _progress: None)

    def forbidden_external_write(*_args, **_kwargs):
        raise AssertionError("dry-run must not invoke external persistence")

    for name in (
        "persist_ai_qualification_cache",
        "persist_scheduler_history",
        "persist_production_metrics",
        "publish_scheduler_observation",
        "persist_operational_slo",
    ):
        monkeypatch.setattr(lifecycle, name, forbidden_external_write)

    result = pipeline.run()

    assert result["status"] == "passed"
    assert result["publication_status"] == "dry-run"
    assert result["release_status"] == "dry-run"
    assert result["derived_state"] == "skipped"
    assert result["production_metrics"] == "skipped"
    assert result["scheduler_observation"] == "skipped"
    assert result["operational_slo"] == "skipped"
    assert result["warnings"] == []
