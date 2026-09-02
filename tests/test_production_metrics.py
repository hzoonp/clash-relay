from __future__ import annotations

import json
from pathlib import Path

from clash_relay.production_metrics import (
    append_metrics_run,
    build_metrics_run,
    empty_metrics,
    parse_metrics_bytes,
)

ROOT = Path(__file__).resolve().parents[1]
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"
PUBLISH_HISTORY = ROOT / "scripts" / "publish_scheduler_history.py"


def _browsing() -> dict:
    return {
        "diagnostics": {
            "tested_nodes": 10,
            "qualified_nodes": 8,
            "failed_nodes": 2,
            "qualified_latency_ms": {"p50": 120.0, "p95": 300.0},
        },
        "stable_nodes": 6,
        "reserve_nodes": 2,
        "scheduler_history": {
            "historically_demoted_nodes": 1,
            "cohort_latency_ema_ms": 140.5,
        },
    }


def _ai() -> dict:
    return {
        "diagnostics": {
            "tested_nodes": 10,
            "probes": {
                "ai_openai": {"qualified_nodes": 3},
                "ai_claude": {"qualified_nodes": 4},
                "ai_gemini": {"qualified_nodes": 8},
            },
            "openai_app": {
                "critical": {
                    "endpoint_count": 4,
                    "tls_errors": 2,
                    "dns_errors": 1,
                    "timeouts": 3,
                },
                "supporting": {
                    "endpoint_count": 4,
                    "tls_errors": 1,
                },
            },
        },
        "qualification_cache": {
            "live_service_probes": 5,
            "cache_pass_hits": 10,
            "cache_fail_hits": 15,
        },
    }


def test_metrics_run_contains_only_aggregate_values(tmp_path: Path) -> None:
    candidate = tmp_path / "config.yaml"
    candidate.write_text("secret-node-payload\n", encoding="utf-8")

    run = build_metrics_run(candidate_path=candidate, browsing=_browsing(), ai=_ai(), epoch=1234)
    serialized = json.dumps(run, sort_keys=True)

    assert "secret-node-payload" not in serialized
    assert "android.chat.openai.com" not in serialized
    assert "cdn.workos.com" not in serialized
    assert run["epoch"] == 1234
    assert run["candidate_bytes"] == len(candidate.read_bytes())
    assert run["browsing"]["qualified"] == 8
    assert run["browsing"]["p95_ms"] == 300.0
    assert run["ai"]["qualified_by_service"] == {
        "ai_claude": 4,
        "ai_gemini": 8,
        "ai_openai": 3,
    }
    assert run["ai"]["live_service_probes"] == 5
    assert run["ai"]["openai_app"] == {
        "app_ready_nodes": 3,
        "critical_endpoint_count": 4,
        "critical_tls_errors": 2,
        "critical_dns_errors": 1,
        "critical_timeouts": 3,
        "supporting_endpoint_count": 4,
        "supporting_tls_errors": 1,
    }


def test_metrics_ring_retains_only_latest_30_runs(tmp_path: Path) -> None:
    candidate = tmp_path / "config.yaml"
    state = empty_metrics()
    for index in range(35):
        candidate.write_text(f"candidate-{index}\n", encoding="utf-8")
        run = build_metrics_run(
            candidate_path=candidate,
            browsing=_browsing(),
            ai=_ai(),
            epoch=1000 + index,
        )
        state = append_metrics_run(state, run)

    assert len(state["runs"]) == 30
    assert state["runs"][0]["epoch"] == 1005
    assert state["runs"][-1]["epoch"] == 1034


def test_duplicate_candidate_sha_refreshes_latest_slot_instead_of_growing(tmp_path: Path) -> None:
    candidate = tmp_path / "config.yaml"
    candidate.write_text("same\n", encoding="utf-8")
    first = build_metrics_run(candidate_path=candidate, browsing=_browsing(), ai=_ai(), epoch=1)
    second = build_metrics_run(candidate_path=candidate, browsing=_browsing(), ai=_ai(), epoch=2)

    state = append_metrics_run(empty_metrics(), first)
    state = append_metrics_run(state, second)

    assert len(state["runs"]) == 1
    assert state["runs"][0]["epoch"] == 2


def test_invalid_metrics_state_safely_resets() -> None:
    state, status = parse_metrics_bytes(b'{"version":1,"runs":[{"bad":"SECRET"}]}')

    assert status == "invalid"
    assert state == empty_metrics()
    assert "SECRET" not in json.dumps(state)


def test_p18_preserves_production_metrics_after_versioned_release_commit() -> None:
    workflow = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    publisher = PUBLISH_HISTORY.read_text(encoding="utf-8")

    release = workflow.index("Publish versioned validated release transaction")
    persist = workflow.index("Persist private scheduler history")
    proof = workflow.index("Record publication result")

    assert release < persist < proof
    assert "continue-on-error: true" in workflow[persist:proof]
    assert 'key_name=f"{production_key}.production-metrics-v1"' in publisher
    assert "metrics = _persist_metrics(" in publisher
    assert '"production_metrics": metrics' in publisher
