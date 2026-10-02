from __future__ import annotations

import json

from clash_relay.source_quality import build_source_quality_report


def _qualification() -> dict:
    return {
        "qualification_removed_unique_nodes": 1,
        "qualification_removed_runtime_entries": 1,
        "removed_by_stage": {
            "transport": {
                "unique_nodes": {"before": 2, "after": 1, "removed": 1},
                "runtime_entries": {"before": 2, "after": 1, "removed": 1, "added": 0},
                "by_source": {"sub_1": 1},
                "unique_by_source": {"sub_1": 1},
                "added_by_source": {},
                "by_region": {"us": 1},
                "by_protocol": {"vless": 1},
                "failure_category": {"timeout": 1},
            }
        },
        "sources_fully_removed": [],
        "ai_service_evidence": {},
    }


def test_source_quality_is_aggregate_only_and_tracks_survival() -> None:
    report = build_source_quality_report(
        source_stage_accounting=[
            {
                "id": "subscription_1",
                "status": "ok",
                "input_nodes": 10,
                "parsed_valid_nodes": 9,
                "skipped_invalid_nodes": 1,
                "post_dedup_nodes": 8,
                "generated_runtime_entries": 8,
                "removed_runtime_entries": 2,
                "final_runtime_entries": 6,
                "removed_at_stage": None,
                "by_stage": {"transport": 2},
            },
            {
                "id": "subscription_2",
                "status": "failed",
                "input_nodes": 0,
                "parsed_valid_nodes": 0,
                "skipped_invalid_nodes": 0,
                "post_dedup_nodes": 0,
                "generated_runtime_entries": 0,
                "removed_runtime_entries": 0,
                "final_runtime_entries": 0,
                "removed_at_stage": None,
                "by_stage": {},
            },
        ],
        qualification=_qualification(),
        candidate={
            "proxy-providers": {
                "cr_general_us": {
                    "type": "inline",
                    "payload": [
                        {
                            "name": "SECRET-US-NODE",
                            "type": "vless",
                            "server": "secret-us.example",
                        }
                    ],
                },
                "cr_browsing_jp": {
                    "type": "inline",
                    "payload": [
                        {
                            "name": "SECRET-JP-NODE",
                            "type": "trojan",
                            "server": "secret-jp.example",
                        }
                    ],
                },
            }
        },
        browsing={
            "diagnostics": {
                "qualified_latency_ms": {
                    "p50": 120.0,
                    "p95": 300.0,
                }
            },
            "scheduler_history": {"cohort_latency_ema_ms": 140.5},
        },
    )

    first = report["sources"][0]
    assert first["parse_rate"] == 0.9
    assert first["admission_rate"] == 0.8889
    assert first["runtime_survival_rate"] == 0.75
    assert first["stability_score"] == 75.0
    assert report["aggregate"]["configured_sources"] == 2
    assert report["aggregate"]["active_sources"] == 1
    assert report["aggregate"]["final_protocol_distribution"] == {
        "trojan": 1,
        "vless": 1,
    }
    assert report["aggregate"]["final_region_distribution"] == {"jp": 1, "us": 1}
    assert report["aggregate"]["qualification_removed_by_protocol"] == {"vless": 1}
    assert report["aggregate"]["qualification_removed_by_region"] == {"us": 1}
    assert report["aggregate"]["browsing_latency"] == {
        "p50_ms": 120.0,
        "p95_ms": 300.0,
        "history_ema_ms": 140.5,
    }

    serialized = json.dumps(report, sort_keys=True)
    assert "SECRET-US-NODE" not in serialized
    assert "SECRET-JP-NODE" not in serialized
    assert "secret-us.example" not in serialized
    assert "secret-jp.example" not in serialized


def test_source_quality_bounds_internal_labels() -> None:
    report = build_source_quality_report(
        source_stage_accounting=[
            {
                "id": "subscription_1",
                "status": "private-status",
                "input_nodes": 1,
                "parsed_valid_nodes": 1,
                "post_dedup_nodes": 1,
                "generated_runtime_entries": 1,
                "final_runtime_entries": 1,
                "removed_at_stage": "private-stage",
                "by_stage": {"private-stage": 999},
            }
        ],
        qualification={
            "qualification_removed_unique_nodes": 0,
            "qualification_removed_runtime_entries": 0,
            "removed_by_stage": {},
            "sources_fully_removed": [],
            "ai_service_evidence": {},
        },
        candidate={},
        browsing={},
    )

    assert report["sources"][0]["status"] == "unknown"
    assert report["sources"][0]["removed_at_stage"] is None
    assert report["sources"][0]["removed_by_stage"] == {}
    assert "private-status" not in json.dumps(report)
    assert "private-stage" not in json.dumps(report)
