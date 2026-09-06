from __future__ import annotations

import json

from clash_relay.scheduler_evidence import compile_scheduler_evidence


def _run(epoch: int, *, attempts: int = 1, recovered: bool = False) -> dict:
    return {
        "epoch": epoch,
        "browsing": {
            "qualified": 8,
            "stable": 6,
            "historically_demoted": 1,
            "regions": {
                "US": {"stable": 3},
                "JP": {"stable": 2},
                "SG": {"stable": 0},
            },
        },
        "ai": {
            "qualified_by_service": {
                "ai_openai": 3,
                "ai_claude": 2,
                "ai_gemini": 4,
            }
        },
        "qualification": {
            "browsing_attempts": attempts,
            "recovered_by_retry": recovered,
        },
        "promotion_guard": {"status": "passed"},
        "private_node_name": "MUST-NOT-LEAK",
        "private_url": "https://secret.example/sub",
    }


def test_scheduler_evidence_starts_observation_only_with_insufficient_history() -> None:
    report = compile_scheduler_evidence({"version": 1, "runs": [_run(100)], "failures": []})

    assert report["status"] == "insufficient_history"
    assert report["mode"] == "observe_only"
    assert report["privacy"] == "aggregate_only"
    assert report["sample_runs"] == 1
    assert report["minimum_sample_runs"] == 3


def test_scheduler_evidence_compiles_region_service_and_retry_aggregates() -> None:
    state = {
        "version": 1,
        "runs": [
            _run(100),
            _run(200, attempts=2, recovered=True),
            _run(300, attempts=3, recovered=False),
        ],
        "failures": [],
    }

    report = compile_scheduler_evidence(state)

    assert report["status"] == "ready"
    assert report["browsing"] == {
        "qualified_nodes": 8,
        "stable_nodes": 6,
        "historically_demoted_nodes": 1,
        "stable_region_count": 2,
        "stable_regions": ["JP", "US"],
    }
    assert report["services"] == {
        "qualified_by_service": {
            "ai_claude": 2,
            "ai_gemini": 4,
            "ai_openai": 3,
        },
        "covered_service_count": 3,
        "service_count": 3,
        "minimum_qualified_nodes": 2,
    }
    assert report["reliability"]["retry_runs"] == 2
    assert report["reliability"]["retry_recoveries"] == 1
    assert report["reliability"]["latest_promotion_guard_status"] == "passed"


def test_scheduler_evidence_failure_trend_uses_bounded_event_history() -> None:
    state = {
        "runs": [_run(100), _run(200), _run(400)],
        "failures": [
            {"epoch": 250, "category": "transient"},
            {"epoch": 300, "category": "transient"},
            {"epoch": 500, "category": "configuration"},
            {"epoch": 600, "category": "configuration"},
        ],
    }

    report = compile_scheduler_evidence(state)

    assert report["reliability"]["recent_failure_rate"] == 0.571
    assert report["reliability"]["recent_failure_streak"] == 2


def test_scheduler_evidence_never_serializes_unrecognized_private_fields() -> None:
    state = {
        "runs": [_run(100), _run(200), _run(300)],
        "failures": [
            {
                "epoch": 400,
                "category": "transient",
                "raw_exception": "PRIVATE-TOKEN https://secret.example/sub",
            }
        ],
        "secret_state": "PRIVATE-TOKEN",
    }

    serialized = json.dumps(compile_scheduler_evidence(state), sort_keys=True)

    assert "MUST-NOT-LEAK" not in serialized
    assert "secret.example" not in serialized
    assert "PRIVATE-TOKEN" not in serialized
