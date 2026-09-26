from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import clash_relay.production_lifecycle as lifecycle
from clash_relay.errors import ValidationError
from clash_relay.production_event_audit import audit_production_preflight_result
from clash_relay.production_lifecycle import ProductionLifecyclePaths
from clash_relay.production_lifecycle_result import ProductionLifecycleResult
from clash_relay.production_preflight import ProductionPreflightPipeline


def test_full_production_preflight_never_invokes_external_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = ProductionLifecyclePaths.canonical(tmp_path)
    for declaration in (paths.config, paths.subscriptions, paths.policies):
        declaration.write_text("fixture: true\n", encoding="utf-8")

    project = SimpleNamespace(config={})
    binary = tmp_path / "fixture-mihomo"
    binary.write_bytes(b"fixture")
    pipeline = ProductionPreflightPipeline(paths)

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
            "release_id": "preflight-fixture",
            "config_sha256": "0" * 64,
        },
    )
    monkeypatch.setattr(pipeline, "_write_lifecycle_observability", lambda _progress: None)

    def forbidden_external_write(*_args, **_kwargs):
        raise AssertionError("production preflight must not invoke external persistence")

    for name in (
        "persist_ai_qualification_cache",
        "persist_scheduler_history",
        "persist_production_metrics",
        "publish_scheduler_observation",
        "persist_operational_slo",
        "commit_carrier_observation_receipt",
    ):
        monkeypatch.setattr(lifecycle, name, forbidden_external_write)
    # The history write path itself stays forbidden even if reached directly.
    monkeypatch.setattr(
        "clash_relay.carrier_history_application.persist_carrier_observation",
        forbidden_external_write,
    )

    result = pipeline.run()

    assert result["status"] == "passed"
    assert result["publication_status"] == "preflight"
    assert result["release_status"] == "preflight"
    assert result["promotion_guard"] == "passed"
    assert result["release_phase"] == "verified"
    assert result["derived_state"] == "skipped"
    assert result["production_metrics"] == "skipped"
    assert result["scheduler_observation"] == "skipped"
    assert result["operational_slo"] == "skipped"
    assert result["carrier_observation_history"] == {"status": "not_configured"}


def test_skipped_preflight_clears_old_receipt(tmp_path: Path) -> None:
    pipeline = ProductionPreflightPipeline(ProductionLifecyclePaths.canonical(tmp_path))
    receipt = pipeline._carrier_receipt_path()
    receipt.parent.mkdir(parents=True)
    receipt.write_text('{"old":"receipt"}', encoding="utf-8")
    assert pipeline.run()["status"] == "skipped"
    assert not receipt.exists()


def _preflight_result(**updates) -> ProductionLifecycleResult:
    document = {
        "status": "passed",
        "publication_status": "preflight",
        "release_status": "preflight",
        "release_phase": "verified",
        "production_pipeline": "passed",
        "promotion_guard": "passed",
        "mihomo_matrix": "passed",
        "proof_status": "passed",
        "manifest_status": "passed",
        "warnings": [],
    }
    document.update(updates)
    return ProductionLifecycleResult.from_mapping(document)


def test_preflight_audit_requires_every_production_relative_gate() -> None:
    audit_production_preflight_result(_preflight_result())

    with pytest.raises(ValidationError, match="promotion_guard"):
        audit_production_preflight_result(_preflight_result(promotion_guard="skipped"))


def test_preflight_audit_rejects_skipped_lifecycle() -> None:
    skipped = ProductionLifecycleResult.from_mapping(
        {
            "status": "skipped",
            "publication_status": "not_applicable",
            "reason": "canonical_declarations_missing",
            "warnings": [],
        }
    )

    with pytest.raises(ValidationError, match="complete rather than skip"):
        audit_production_preflight_result(skipped)


def test_preflight_audit_rejects_dry_run_identity() -> None:
    dry_run = _preflight_result(
        publication_status="dry-run",
        release_status="dry-run",
    )

    with pytest.raises(ValidationError, match="preflight lifecycle status"):
        audit_production_preflight_result(dry_run)


def test_preflight_audit_rejects_wrong_release_identity() -> None:
    with pytest.raises(ValidationError, match="release_status='preflight'"):
        audit_production_preflight_result(_preflight_result(release_status="dry-run"))


def test_preflight_result_requires_verified_phase() -> None:
    with pytest.raises(ValidationError, match="preflight lifecycle result must be verified"):
        ProductionLifecycleResult.from_mapping(
            {
                "status": "passed",
                "publication_status": "preflight",
                "release_status": "preflight",
                "release_phase": "promoted",
                "warnings": [],
            }
        )


def test_canonical_runner_exposes_explicit_preflight_mode() -> None:
    runner = Path("scripts/run_production_release.py").read_text(encoding="utf-8")

    assert '"--production-preflight"' in runner
    assert "ProductionPreflightPipeline(" in runner
    assert "audit_production_preflight_result(result)" in runner
    assert "if publish:" in runner


def test_preflight_audit_accepts_non_writing_carrier_statuses() -> None:
    for status in ("not_configured", "receipt_issued", "skipped"):
        audit_production_preflight_result(
            _preflight_result(carrier_observation_history={"status": status})
        )


def test_preflight_audit_rejects_carrier_history_write_evidence() -> None:
    with pytest.raises(ValidationError, match="must never persist carrier observation history"):
        audit_production_preflight_result(
            _preflight_result(carrier_observation_history={"status": "published"})
        )


def test_preflight_with_carrier_input_issues_receipt_without_any_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = ProductionLifecyclePaths.canonical(
        tmp_path, carrier_qualification_input=tmp_path / "carrier-input.json"
    )
    for declaration in (paths.config, paths.subscriptions, paths.policies):
        declaration.write_text("fixture: true\n", encoding="utf-8")
    (tmp_path / "carrier-input.json").write_text("{}", encoding="utf-8")

    project = SimpleNamespace(config={})
    binary = tmp_path / "fixture-mihomo"
    binary.write_bytes(b"fixture")
    pipeline = ProductionPreflightPipeline(paths)

    monkeypatch.setattr(lifecycle.ProjectPaths, "load", lambda _self: project)
    monkeypatch.setattr(lifecycle, "publication_gate", lambda *_args, **_kwargs: None)

    def stage_carrier_input() -> None:
        snapshot = paths.private_dir / "carrier-qualification.json"
        snapshot.write_text("{}", encoding="utf-8")
        pipeline._carrier_input_snapshot = snapshot
        pipeline._carrier_binding_passed = True

    monkeypatch.setattr(pipeline, "_stage_carrier_input", stage_carrier_input)
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
        lambda **_kwargs: {"release_id": "preflight-fixture", "config_sha256": "0" * 64},
    )
    monkeypatch.setattr(pipeline, "_write_lifecycle_observability", lambda _progress: None)
    monkeypatch.setattr(
        lifecycle,
        "build_carrier_observation_receipt",
        lambda **_kwargs: {"receipt_schema_version": 1},
    )

    def forbidden_external_write(*_args, **_kwargs):
        raise AssertionError("preflight with carrier input must not invoke external persistence")

    for name in (
        "persist_ai_qualification_cache",
        "persist_scheduler_history",
        "persist_production_metrics",
        "publish_scheduler_observation",
        "persist_operational_slo",
        "commit_carrier_observation_receipt",
    ):
        monkeypatch.setattr(lifecycle, name, forbidden_external_write)
    # The history write path itself stays forbidden even if reached directly.
    monkeypatch.setattr(
        "clash_relay.carrier_history_application.persist_carrier_observation",
        forbidden_external_write,
    )

    result = pipeline.run()

    assert result["status"] == "passed"
    assert result["publication_status"] == "preflight"
    assert result["carrier_observation_history"] == {
        "status": "receipt_issued",
        "receipt": "carrier-observation-receipt.json",
    }
    assert result["warnings"] == []
    assert pipeline._carrier_receipt_path().is_file()

    def failed_proof(*, release):
        raise ValidationError("proof gate failed")

    monkeypatch.setattr(pipeline, "_post_commit_proof", failed_proof)
    with pytest.raises(ValidationError, match="proof gate failed"):
        pipeline.run()
    assert not pipeline._carrier_receipt_path().exists()
