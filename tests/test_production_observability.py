from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import clash_relay.production_observability as observability
from clash_relay.operational_slo import ProductionOutcome
from clash_relay.production_metrics import build_metrics_run, parse_metrics_bytes
from clash_relay.qualification_reliability import (
    QualificationFailureCategory,
    QualificationStageRejected,
)


def _browsing() -> dict:
    return {
        "diagnostics": {
            "tested_nodes": 5,
            "qualified_nodes": 4,
            "failed_nodes": 1,
            "qualified_latency_ms": {"p50": 80.0, "p95": 140.0},
        },
        "stable_nodes": 3,
        "reserve_nodes": 1,
        "scheduler_history": {
            "historically_demoted_nodes": 1,
            "cohort_latency_ema_ms": 100.0,
            "regions": {
                "US": {
                    "tested": 3,
                    "qualified": 3,
                    "stable": 2,
                    "preferred_stable": 2,
                    "historically_demoted": 0,
                }
            },
        },
    }


def _ai() -> dict:
    return {
        "diagnostics": {
            "tested_nodes": 4,
            "probes": {"ai_openai": {"qualified_nodes": 3}},
        },
        "qualification_cache": {"live_service_probes": 4},
    }


def test_metrics_add_release_matrix_regions_and_safe_timings(tmp_path: Path) -> None:
    candidate = tmp_path / "config.yaml"
    candidate.write_text("private proxy payload\n", encoding="utf-8")
    sha = __import__("hashlib").sha256(candidate.read_bytes()).hexdigest()
    run = build_metrics_run(
        candidate_path=candidate,
        browsing=_browsing(),
        ai=_ai(),
        qualification={
            "status": "qualified",
            "stages": [{"name": "generated", "fingerprint": "SECRET-FINGERPRINT"}],
            "timings_ms": {"browsing_transport": 12.5, "ai": 22.0, "total": 34.5},
            "private_detail": "SECRET-NODE",
        },
        release={
            "status": "published",
            "release_id": sha,
            "previous_release_id": "a" * 64,
            "production_changed": True,
            "private_detail": "SECRET-SERVER",
        },
        mihomo_matrix={
            "status": "passed",
            "validated_cores": ["v1", "v2"],
            "results": [{"stderr": "SECRET-CORE-OUTPUT"}],
        },
        epoch=10,
    )
    serialized = json.dumps(run, sort_keys=True)

    assert run["browsing"]["regions"]["US"]["qualified"] == 3
    assert run["release"]["status"] == "published"
    assert run["mihomo"]["validated_core_count"] == 2
    assert run["performance"]["total"] == 34.5
    assert run["qualification"] == {"status": "qualified", "stage_count": 1}
    for secret in (
        "private proxy payload",
        "SECRET-FINGERPRINT",
        "SECRET-NODE",
        "SECRET-SERVER",
        "SECRET-CORE-OUTPUT",
    ):
        assert secret not in serialized


def test_metrics_parser_strips_unknown_fields_from_existing_private_state() -> None:
    document = {
        "version": 1,
        "runs": [
            {
                "epoch": 1,
                "candidate_sha256": "b" * 64,
                "candidate_bytes": 10,
                "browsing": {},
                "ai": {},
                "accidental_secret": "https://secret.example/subscription",
            }
        ],
    }

    state, status = parse_metrics_bytes(json.dumps(document).encode())

    assert status == "loaded"
    serialized = json.dumps(state)
    assert "accidental_secret" not in serialized
    assert "secret.example" not in serialized



def _project():
    return SimpleNamespace(
        config={"publishing": {"cloudflare_kv": {"key": "production-config"}}}
    )


def test_candidate_identity_prefers_qualified_candidate(tmp_path: Path) -> None:
    private_dir = tmp_path / "private"
    private_dir.mkdir()
    (private_dir / "generated.yaml").write_bytes(b"generated-candidate")

    generated_sha = hashlib.sha256(b"generated-candidate").hexdigest()
    assert observability._candidate_identity(private_dir) == (
        generated_sha,
        len(b"generated-candidate"),
    )

    (private_dir / "config.yaml").write_bytes(b"qualified-candidate")
    qualified_sha = hashlib.sha256(b"qualified-candidate").hexdigest()
    assert observability._candidate_identity(private_dir) == (
        qualified_sha,
        len(b"qualified-candidate"),
    )


def test_candidate_identity_handles_missing_or_empty_files(tmp_path: Path) -> None:
    private_dir = tmp_path / "private"
    private_dir.mkdir()
    (private_dir / "generated.yaml").write_bytes(b"")

    assert observability._candidate_identity(private_dir) == (None, None)


def test_retry_and_promotion_state_are_fail_closed(tmp_path: Path) -> None:
    private_dir = tmp_path / "private"
    private_dir.mkdir()

    assert observability._qualification_retry_state(private_dir) == (False, False)
    observability._write_json(
        private_dir / "qualification-pipeline-summary.json",
        {"browsing": {"stage_attempts": 2, "recovered_by_retry": True}},
    )
    assert observability._qualification_retry_state(private_dir) == (True, True)

    observability._write_json(
        private_dir / "qualification-pipeline-summary.json",
        {"browsing": {"stage_attempts": True, "recovered_by_retry": True}},
    )
    assert observability._qualification_retry_state(private_dir) == (False, True)

    (private_dir / "qualification-pipeline-summary.json").write_text(
        "not-json",
        encoding="utf-8",
    )
    assert observability._qualification_retry_state(private_dir) == (False, False)

    guard = private_dir / "promotion-guard.json"
    assert observability._promotion_state(private_dir) == (False, False)
    observability._write_json(guard, {"status": "passed"})
    assert observability._promotion_state(private_dir) == (True, False)
    observability._write_json(guard, {"status": "blocked"})
    assert observability._promotion_state(private_dir) == (True, True)
    observability._write_json(guard, {"status": "unknown"})
    assert observability._promotion_state(private_dir) == (False, False)
    guard.write_text("not-json", encoding="utf-8")
    assert observability._promotion_state(private_dir) == (False, False)


def test_dry_run_observability_does_not_touch_external_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any):
        raise AssertionError("dry-run must not publish observability")

    monkeypatch.setattr(observability, "persist_production_metrics", forbidden)
    monkeypatch.setattr(observability, "_publish_scheduler_observation", forbidden)
    monkeypatch.setattr(observability, "_persist_operational_slo", forbidden)

    result = observability.publish_post_release_observability(
        project=_project(),  # type: ignore[arg-type]
        publish=False,
        private_dir=tmp_path,
        lifecycle_started=0.0,
        env={},
    )

    assert result.production_metrics == {"status": "skipped", "reason": "dry_run"}
    assert result.scheduler_observation == {"status": "skipped", "reason": "dry_run"}
    assert result.operational_slo == {"status": "skipped", "reason": "dry_run"}
    assert result.warnings == ()


def test_scheduler_observation_requires_freshly_published_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path.mkdir(exist_ok=True)

    monkeypatch.setattr(
        observability,
        "persist_production_metrics",
        lambda **_kwargs: {"status": "unavailable"},
    )

    def forbidden(*_args: Any, **_kwargs: Any):
        raise AssertionError("stale metrics must not produce scheduler evidence")

    monkeypatch.setattr(observability, "_publish_scheduler_observation", forbidden)

    result = observability.publish_post_release_observability(
        project=_project(),  # type: ignore[arg-type]
        publish=True,
        private_dir=tmp_path,
        lifecycle_started=0.0,
        env={},
    )

    assert result.scheduler_observation == {
        "status": "skipped",
        "reason": "production_metrics_not_published",
    }
    assert "persist_production_metrics" in result.warnings


def test_post_release_observability_owns_optional_publish_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def metrics(**_kwargs: Any) -> dict[str, Any]:
        calls.append("metrics")
        return {"status": "published", "runs": 3}

    def scheduler(**_kwargs: Any) -> dict[str, Any]:
        calls.append("scheduler")
        return {"status": "published", "sample_runs": 3}

    def slo(**_kwargs: Any) -> dict[str, Any]:
        calls.append("slo")
        return {"status": "published", "attempts": 5}

    monkeypatch.setattr(observability, "persist_production_metrics", metrics)
    monkeypatch.setattr(observability, "_publish_scheduler_observation", scheduler)
    monkeypatch.setattr(observability, "_record_operational_slo", slo)

    result = observability.publish_post_release_observability(
        project=_project(),  # type: ignore[arg-type]
        publish=True,
        private_dir=tmp_path,
        lifecycle_started=0.0,
        env={},
    )

    assert calls == ["metrics", "scheduler", "slo"]
    assert result.production_metrics["status"] == "published"
    assert result.scheduler_observation["status"] == "published"
    assert result.operational_slo["status"] == "published"
    assert result.warnings == ()
    assert observability._load_json(tmp_path / "production-metrics-publish.json")[
        "status"
    ] == "published"
    assert observability._load_json(tmp_path / "scheduler-observation-publish.json")[
        "status"
    ] == "published"



def test_failure_observability_classifies_typed_qualification_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rejection = QualificationStageRejected(
        stage="browsing",
        category=QualificationFailureCategory.TRANSIENT,
        retryable=True,
    )
    wrapped = RuntimeError("aggregate qualification failure")
    wrapped.__cause__ = rejection
    recorded: dict[str, Any] = {}

    def record(**kwargs: Any) -> dict[str, Any]:
        recorded.update(kwargs)
        return {"status": "published"}

    monkeypatch.setattr(observability, "_record_operational_slo", record)

    result = observability.record_failure_observability(
        project=_project(),  # type: ignore[arg-type]
        publish=True,
        private_dir=tmp_path,
        lifecycle_started=0.0,
        error=wrapped,
        env={},
    )

    assert recorded["outcome"] is ProductionOutcome.QUALIFICATION_REJECTED
    assert recorded["failure_category"] == QualificationFailureCategory.TRANSIENT.value
    assert recorded["failure_retry_attempted"] is True
    assert result.operational_slo == {"status": "published"}
    assert result.warnings == ()
