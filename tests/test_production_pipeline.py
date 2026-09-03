from __future__ import annotations

from types import SimpleNamespace

from clash_relay import production_pipeline
from clash_relay.production_pipeline import audit_candidate, render_qualification_summary_markdown


def test_audit_candidate_is_the_single_composite_audit_boundary(monkeypatch) -> None:
    project = SimpleNamespace(acl4ssr=None)
    candidate = {"proxy-groups": [], "proxy-providers": {}}

    monkeypatch.setattr(
        production_pipeline,
        "audit_production_candidate",
        lambda project, candidate, build_report=None: {"status": "passed"},
    )
    monkeypatch.setattr(
        production_pipeline,
        "audit_routing_v2",
        lambda project, candidate: {"status": "passed"},
    )
    monkeypatch.setattr(
        production_pipeline,
        "audit_route_lock",
        lambda candidate: {"status": "passed"},
    )
    monkeypatch.setattr(
        production_pipeline,
        "audit_openai_client_path",
        lambda candidate, allow_legacy_server_qualified=False: {
            "status": "passed",
            "legacy": allow_legacy_server_qualified,
        },
    )

    result = audit_candidate(
        project,  # type: ignore[arg-type]
        candidate,
        build_report={"subscriptions": []},
        allow_legacy_openai_client_path=True,
    )

    assert result["status"] == "passed"
    assert result["routing_v2"]["status"] == "passed"
    assert result["openai_app"]["status"] == "passed"
    assert result["openai_client_path"] == {"status": "passed", "legacy": True}


def test_qualification_summary_is_aggregate_only() -> None:
    browsing = {
        "stable_nodes": 3,
        "reserve_nodes": 2,
        "automatic_nodes": 4,
        "diagnostics": {
            "tested_nodes": 7,
            "qualified_nodes": 5,
            "failed_nodes": 2,
            "required_successes": 2,
            "attempts_per_node": 3,
            "qualified_latency_ms": {"p50": 80, "p95": 160},
        },
        "scheduler_history": {
            "status": "loaded",
            "records_before": 4,
            "records_after": 5,
            "historically_demoted_nodes": 1,
        },
    }
    ai = {
        "diagnostics": {
            "tested_nodes": 5,
            "selector_failures": 1,
            "probes": {
                "openai": {"live_tested_nodes": 3, "qualified_nodes": 2},
            },
        },
        "qualification_cache": {
            "live_service_probes": 3,
            "cache_pass_hits": 1,
            "cache_fail_hits": 1,
        },
    }

    markdown = render_qualification_summary_markdown(browsing, ai)

    assert "Tested nodes: **7**" in markdown
    assert "`openai` | 3 | 2" in markdown
    assert "server" not in markdown.lower()
    assert "credential" not in markdown.lower()
