from __future__ import annotations

import json

import pytest

from clash_relay.errors import ValidationError
from clash_relay.qualification_observability import (
    render_qualification_observability_markdown,
    safe_qualification_observability,
)


def _private_summary() -> dict:
    return {
        "qualification_removed_unique_nodes": 1,
        "qualification_removed_runtime_entries": 2,
        "removed_by_stage": {
            "browsing": {
                "unique_nodes": {"before": 1, "after": 1, "removed": 0},
                "runtime_entries": {"before": 2, "after": 1, "removed": 1},
                "by_source": {"sub_4": 1},
                "failure_category": {"browsing_qualification_failed": 1},
                "node": "SECRET-NODE",
            },
            "ai": {
                "unique_nodes": {"before": 1, "after": 0, "removed": 1},
                "runtime_entries": {"before": 1, "after": 0, "removed": 1},
                "by_source": {"sub_4": 1},
                "failure_category": {"ai_qualification_failed": 1},
            },
        },
        "sources_fully_removed": [
            {
                "source": "sub_4",
                "unique_nodes": 1,
                "final_unique_nodes": 0,
                "runtime_entries": 2,
                "final_runtime_entries": 0,
                "removed_at_stage": "ai",
                "by_stage": {"browsing": 1, "ai": 1},
                "by_failure_category": {
                    "browsing_qualification_failed": 1,
                    "ai_qualification_failed": 1,
                },
                "server": "secret.example.invalid",
            }
        ],
        "ai": {
            "service_evidence": {
                "openai": {
                    "evidence_status": "inconclusive",
                    "evidence_source": "cache",
                    "systemic_failure_detected": True,
                    "lkg_fresh": True,
                    "live_tested": 2,
                    "live_passed": 0,
                    "live_failed": 0,
                    "inconclusive": 2,
                    "blocked_critical_endpoints": ["https://secret.example.invalid/path"],
                    "credential": "SECRET-TOKEN",
                }
            }
        },
    }


@pytest.mark.parametrize(
    "status,source,systemic,lkg",
    [
        ("passed", "live", False, False),
        ("passed", "cache", False, True),
        ("inconclusive", "cache", True, True),
        ("inconclusive", "none", True, False),
    ],
)
def test_aggregate_projection_is_private_and_deterministic(status, source, systemic, lkg) -> None:
    private = _private_summary()
    evidence = private["ai"]["service_evidence"]["openai"]
    evidence.update(
        evidence_status=status,
        evidence_source=source,
        systemic_failure_detected=systemic,
        lkg_fresh=lkg,
    )
    safe = safe_qualification_observability(private)
    markdown = render_qualification_observability_markdown(private)
    assert safe["qualification_removed_unique_nodes"] == 1
    assert safe["qualification_removed_runtime_entries"] == 2
    assert safe["sources_fully_removed"][0]["removed_at_stage"] == "ai"
    assert safe["ai_service_evidence"]["openai"]["blocked_critical_endpoint_count"] == 1
    assert "| sub_4 | ai | 1 | 2 |" in markdown
    assert "browsing_qualification_failed" in markdown
    assert json.dumps(safe, sort_keys=True) == json.dumps(
        safe_qualification_observability(private), sort_keys=True
    )
    for secret in ("SECRET-NODE", "SECRET-TOKEN", "secret.example.invalid", "https://"):
        assert secret not in json.dumps(safe)
        assert secret not in markdown


def test_projection_rejects_identity_bearing_labels() -> None:
    private = _private_summary()
    private["sources_fully_removed"][0]["source"] = "secret.example.invalid"
    with pytest.raises(ValidationError, match="source label"):
        safe_qualification_observability(private)
