from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from clash_relay.carrier_qualification import run_carrier_qualification, safe_carrier_report
from clash_relay.production_proof import build_production_proof, render_production_proof_markdown
from clash_relay.qualification_observability import (
    render_qualification_observability_markdown,
    safe_qualification_observability,
)
from clash_relay.release_manifest import build_release_manifest, render_release_manifest_markdown


def test_stale_full_campaign_keeps_coverage_in_public_projection_and_actions() -> None:
    report = run_carrier_qualification(
        {
            "schema_version": 1,
            "collected_at_epoch": 1_000,
            "carriers": {
                carrier: {"tested": 5, "reachable": 5, "median_latency_ms": 30}
                for carrier in ("telecom", "unicom", "mobile")
            },
        },
        now_epoch=1_000 + 7 * 3600,
    )
    safe = safe_carrier_report(report)
    projected = safe_qualification_observability({"carrier_qualification": report})
    rendered = render_qualification_observability_markdown({"carrier_qualification": report})

    for value in (report, safe, projected["carrier_qualification"]):
        assert value["status"] == value["coverage"] == "full"
        assert value["freshness"]["status"] == "stale"
        assert value["evidence"]["status"] == "stale"
    assert "Coverage: **full**" in rendered
    assert "Freshness: **stale**" in rendered
    assert "Evidence: **stale**" in rendered
    assert "Coverage: **stale**" not in rendered


def test_unconfigured_projection_has_explicit_coverage() -> None:
    safe = safe_carrier_report(run_carrier_qualification())
    assert safe["status"] == safe["coverage"] == "not_configured"


def test_stale_coverage_is_separate_in_proof_and_manifest(tmp_path: Path) -> None:
    carrier = run_carrier_qualification(
        {
            "schema_version": 1,
            "collected_at_epoch": 1000,
            "carriers": {
                name: {"tested": 5, "reachable": 5, "median_latency_ms": 30}
                for name in ("telecom", "unicom", "mobile")
            },
        },
        now_epoch=1000 + 7 * 3600,
    )
    qualification = {"status": "qualified", "reachability": {"carrier_qualification": carrier}}
    candidate = {"proxy-providers": {}, "proxy-groups": [], "rule-providers": {}, "rules": []}
    candidate_path = tmp_path / "candidate.yaml"
    candidate_path.write_text(
        "proxy-providers: {}\nproxy-groups: []\nrule-providers: {}\nrules: []\n",
        encoding="utf-8",
    )
    audit = {"status": "passed", "reachability": {"status": "passed"}, "subscriptions": []}
    proof = build_production_proof(
        candidate_path=candidate_path,
        audit=audit,
        browsing={"status": "qualified", "diagnostics": {}},
        ai={"status": "qualified", "diagnostics": {}, "service_qualified_nodes": {}},
        validated_cores=("v1.19.29", "v1.19.30"),
        publication_status="preflight",
        qualification=qualification,
    )
    manifest = build_release_manifest(
        candidate=candidate,
        candidate_bytes=candidate_path.read_bytes(),
        audit=audit,
        qualification=qualification,
        promotion_guard=None,
        matrix={"status": "passed", "validated_cores": ["v1.19.29", "v1.19.30"]},
        release=None,
        publication_status="preflight",
        policy_model_version=2,
        generated_at=datetime(2026, 9, 26, tzinfo=UTC),
    )
    for report, markdown in (
        (proof["qualification_pipeline"], render_production_proof_markdown(proof)),
        (manifest["qualification"], render_release_manifest_markdown(manifest)),
    ):
        projected = report["carrier_qualification"]
        assert projected["coverage"] == "full"
        assert projected["freshness"]["status"] == "stale"
        assert projected["evidence"]["status"] == "stale"
        assert "Coverage: **full**" in markdown
        assert "Coverage: **stale**" not in markdown
