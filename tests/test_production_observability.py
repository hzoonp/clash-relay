from __future__ import annotations

import json
from pathlib import Path

from clash_relay.production_metrics import build_metrics_run, parse_metrics_bytes


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
    for secret in ("private proxy payload", "SECRET-FINGERPRINT", "SECRET-NODE", "SECRET-SERVER", "SECRET-CORE-OUTPUT"):
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
