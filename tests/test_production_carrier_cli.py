from __future__ import annotations

import importlib.util
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from clash_relay.production_lifecycle import ProductionLifecyclePaths, ProductionPipeline
from clash_relay.production_proof import build_production_proof, render_production_proof_markdown
from clash_relay.qualification_observability import render_qualification_observability_markdown
from clash_relay.qualification_pipeline import _carrier_report
from clash_relay.release_manifest import build_release_manifest, render_release_manifest_markdown


def test_cli_passes_external_carrier_input_into_canonical_preflight(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts/run_production_release.py"
    spec = importlib.util.spec_from_file_location("production_carrier_runner", script)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    external = tmp_path / "carrier-aggregate.json"
    external.write_text("{}", encoding="utf-8")
    captured: list[Path | None] = []

    class FixturePreflight:
        def __init__(self, paths, *, workers):
            captured.append(paths.carrier_qualification_input)
            assert workers == 12

        def run(self):
            return {
                "status": "passed",
                "publication_status": "preflight",
                "release_phase": "verified",
                "warnings": [],
            }

    monkeypatch.setattr(runner, "ProductionPreflightPipeline", FixturePreflight)
    monkeypatch.setattr(runner, "audit_production_preflight_result", lambda _result: None)
    code = runner.main(
        [
            "--root",
            str(tmp_path),
            "--production-preflight",
            "--carrier-qualification-input",
            str(external),
        ]
    )
    assert code == 0
    assert captured == [external]
    assert json.loads(capsys.readouterr().out)["publication_status"] == "preflight"


def test_external_carrier_aggregate_reaches_actions_proof_and_manifest(tmp_path: Path) -> None:
    external = tmp_path / "carrier-input.json"
    external.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "collected_at_epoch": int(time.time()),
                "sample_set_id": "a" * 32,
                "inventory_set_id": "b" * 32,
                "probe_plan_id": "c" * 32,
                "sampler_version": 2,
                "carriers": {
                    carrier: {
                        "sampled": 6,
                        "sampled_tcp_endpoints": 6,
                        "tested": 6,
                        "reachable": 6,
                        "skipped_unsupported": 1,
                        "skipped_udp_native_endpoints": 1,
                        "geographic_regions_sampled": 2,
                        "protocols_sampled": 1,
                        "sources_sampled": 1,
                        "strata_sampled": 2,
                        "sufficient_evidence": True,
                        "median_latency_ms": latency,
                        "p90_latency_ms": latency,
                    }
                    for carrier, latency in (("telecom", 40), ("unicom", 50), ("mobile", 60))
                },
            }
        ),
        encoding="utf-8",
    )
    pipeline = ProductionPipeline(
        ProductionLifecyclePaths.canonical(tmp_path, carrier_qualification_input=external),
        publish=False,
    )
    pipeline._prepare_dirs()
    pipeline._stage_carrier_input()
    carrier = _carrier_report(pipeline._carrier_input_snapshot)
    assert carrier["status"] == "full"
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
    for projected in (proof["qualification_pipeline"], manifest["qualification"]):
        assert projected["carrier_qualification"]["status"] == "full"
        assert projected["carrier_qualification"]["aggregate"]["tested"] == 18
        assert projected["carrier_qualification"]["sample_set_id"] == "a" * 32
        assert projected["carrier_qualification"]["evidence"]["status"] == "sufficient"
    for markdown in (
        render_qualification_observability_markdown(qualification),
        render_production_proof_markdown(proof),
        render_release_manifest_markdown(manifest),
    ):
        assert "Carrier qualification (advisory)" in markdown
        assert "external_self_hosted_advisory" in markdown
        assert "| telecom | 6 | 6 | 40.0 |" in markdown
    assert json.dumps(manifest, sort_keys=True) == json.dumps(
        build_release_manifest(
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
        ),
        sort_keys=True,
    )


def test_pipeline_cli_accepts_external_carrier_input(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts/run_production_pipeline.py"
    spec = importlib.util.spec_from_file_location("production_pipeline_cli", script)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    parser = runner._parser()
    args = parser.parse_args(
        [
            "--candidate",
            "c.yaml",
            "--output",
            "out.yaml",
            "--mihomo-bin",
            "mihomo",
            "--stage-dir",
            "stages",
            "--browsing-report",
            "b.json",
            "--ai-report",
            "a.json",
            "--pre-audit",
            "pre.json",
            "--post-audit",
            "post.json",
            "--qualification-report",
            "q.json",
            "--carrier-qualification-input",
            "carrier.json",
        ]
    )
    assert args.carrier_qualification_input == Path("carrier.json")
