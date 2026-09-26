from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from clash_relay.carrier_collector import collect_carrier_probes
from clash_relay.carrier_probe import (
    assert_self_hosted_probe_environment,
    build_carrier_probe_payload,
    probe_tcp_endpoint,
    run_carrier_probe,
    sample_probe_targets,
)
from clash_relay.carrier_qualification import run_carrier_qualification, safe_carrier_report
from clash_relay.errors import ValidationError

KEY = b"one-repository-only-secret-key-value-123456"
REPO = "hzoonp/clash-relay"


def candidate() -> dict:
    providers = {}
    for source in ("subscription_1", "subscription_2"):
        for region in ("HK", "JP"):
            payload = []
            for protocol in ("ss", "trojan"):
                for index in range(4):
                    payload.append(
                        {
                            "name": f"[{region}] {source}/secret-name-{index} #0123456789",
                            "type": protocol,
                            "server": f"{source}-{region}-{protocol}-{index}.example.invalid",
                            "port": 1000 + index,
                            "password": "secret-credential",
                        }
                    )
            payload.append(
                {
                    "name": f"[{region}] {source}/udp #0123456789",
                    "type": "hysteria2",
                    "server": f"udp-{source}-{region}.example.invalid",
                    "port": 443,
                }
            )
            providers[f"{source}_{region}"] = {"payload": payload}
    return {"proxy-providers": providers}


def payload(
    carrier: str,
    sample_id: str = "a" * 32,
    timestamp: int = 1000,
    sampled: int = 6,
    tested: int = 6,
) -> dict:
    return {
        "schema_version": 1,
        "sample_set_id": sample_id,
        "inventory_set_id": "b" * 32,
        "probe_plan_id": "c" * 32,
        "sampler_version": 3,
        "collected_at_epoch": timestamp,
        "carriers": {
            carrier: {
                "sampled": sampled,
                "sampled_tcp_endpoints": sampled,
                "tested": tested,
                "reachable": 0,
                "skipped_unsupported": 2,
                "skipped_udp_native_endpoints": 2,
                "geographic_regions_sampled": 1,
                "protocols_sampled": 1,
                "sources_sampled": 1 if sampled < 5 else 2,
                "strata_sampled": 1 if sampled < 5 else 2,
                "eligible_tcp_endpoints": sampled,
                "outcomes": {
                    "dns_failure": tested,
                    "connect_timeout": 0,
                    "connection_refused": 0,
                    "connect_failure": 0,
                    "tcp_connected": 0,
                },
                "median_latency_ms": None,
                "p90_latency_ms": None,
                "sufficient_evidence": sampled >= 5 and tested == sampled,
            }
        },
    }


def test_deterministic_balanced_sampling_and_repository_bound_identity() -> None:
    first = sample_probe_targets(candidate=candidate(), key=KEY, repository=REPO)
    second = sample_probe_targets(candidate=candidate(), key=KEY, repository=REPO)
    assert first == second
    assert first.sampled == 12
    assert len({t.identity for t in first.targets}) == 12
    assert len({t.region for t in first.targets}) == 2
    assert len({t.protocol for t in first.targets}) == 2
    assert len({t.source for t in first.targets}) == 2
    assert first.skipped_unsupported == 4
    assert (
        first.sample_set_id
        != sample_probe_targets(
            candidate=candidate(), key=KEY, repository="other/repo"
        ).sample_set_id
    )
    with pytest.raises(ValidationError, match="bounded"):
        sample_probe_targets(candidate=candidate(), key=KEY, repository=REPO, max_targets=49)


def test_three_carriers_share_sample_set_and_collector_fails_closed() -> None:
    inputs = [payload(carrier) for carrier in ("telecom", "unicom", "mobile")]
    merged = collect_carrier_probes(inputs, now_epoch=1001)
    assert merged["sample_set_id"] == "a" * 32
    report = safe_carrier_report(run_carrier_qualification(merged, now_epoch=1001))
    assert report["coverage"] == "full"
    assert report["evidence"]["status"] == "sufficient"
    assert report["aggregate"] == {
        "carriers_reported": 3,
        "tested": 18,
        "reachable": 0,
        "reachable_ratio": 0.0,
    }
    mismatch = [*inputs[:2], payload("mobile", "b" * 32)]
    with pytest.raises(ValidationError, match="sample sets differ"):
        collect_carrier_probes(mismatch, now_epoch=1001)
    with pytest.raises(ValidationError, match="repeats a carrier"):
        collect_carrier_probes([inputs[0], inputs[0], inputs[2]], now_epoch=1001)
    with pytest.raises(ValidationError, match="timestamp drift"):
        collect_carrier_probes([*inputs[:2], payload("mobile", timestamp=1301)], now_epoch=1301)


def test_insufficient_stale_and_zero_reachable_are_advisory() -> None:
    inputs = [payload(carrier, sampled=1, tested=1) for carrier in ("telecom", "unicom", "mobile")]
    merged = collect_carrier_probes(inputs, now_epoch=1001)
    report = run_carrier_qualification(merged, now_epoch=1001)
    assert report["status"] == "full"
    assert report["evidence"]["status"] == "insufficient"
    stale = run_carrier_qualification(merged, now_epoch=1000 + 7 * 3600)
    assert stale["evidence"]["status"] == "stale"
    assert safe_carrier_report(stale)["coverage"] == "full"
    with pytest.raises(ValidationError, match="stale"):
        collect_carrier_probes(inputs, now_epoch=1000 + 7 * 3600)


def test_inventory_plan_and_sampler_drift_fail_closed() -> None:
    baseline = [payload(carrier) for carrier in ("telecom", "unicom", "mobile")]
    inventory_drift = [*baseline[:2], payload("mobile")]
    inventory_drift[2]["inventory_set_id"] = "d" * 32
    with pytest.raises(ValidationError, match="inventories differ"):
        collect_carrier_probes(inventory_drift, now_epoch=1001)
    plan_drift = [*baseline[:2], payload("mobile")]
    plan_drift[2]["probe_plan_id"] = "d" * 32
    with pytest.raises(ValidationError, match="probe plans differ"):
        collect_carrier_probes(plan_drift, now_epoch=1001)
    version_drift = [*baseline[:2], payload("mobile")]
    version_drift[2]["sampler_version"] += 1
    with pytest.raises(ValidationError, match="sampler version"):
        collect_carrier_probes(version_drift, now_epoch=1001)


@pytest.mark.parametrize(
    "update",
    [
        {"reachable": 1, "median_latency_ms": None},
        {"reachable": 1, "median_latency_ms": 50, "p90_latency_ms": 49},
        {"sampled": 49, "sampled_tcp_endpoints": 49, "tested": 49},
        {"sampled": 5, "sampled_tcp_endpoints": 5, "tested": 6},
        {"skipped_unsupported": 100_001, "skipped_udp_native_endpoints": 100_001},
        {"geographic_regions_sampled": 8},
    ],
)
def test_malformed_carrier_aggregate_fails_closed(update: dict) -> None:
    candidate_payload = payload("telecom")
    candidate_payload["carriers"]["telecom"].update(update)
    with pytest.raises(ValidationError):
        run_carrier_qualification(candidate_payload, now_epoch=1001)


def test_single_repeated_stratum_is_insufficient_despite_five_samples() -> None:
    candidate_payload = payload("telecom")
    row = candidate_payload["carriers"]["telecom"]
    row["sources_sampled"] = 1
    row["strata_sampled"] = 1
    row["eligible_tcp_endpoints"] = 20
    row["sufficient_evidence"] = False
    report = run_carrier_qualification(candidate_payload, now_epoch=1001)
    assert report["coverage"] == "partial"
    assert report["evidence"]["status"] == "insufficient"


def test_protocol_labels_alone_do_not_make_evidence_sufficient() -> None:
    candidate_payload = payload("telecom")
    row = candidate_payload["carriers"]["telecom"]
    row.update(
        {
            "sources_sampled": 1,
            "geographic_regions_sampled": 1,
            "protocols_sampled": 2,
            "strata_sampled": 2,
            "eligible_tcp_endpoints": 20,
            "sufficient_evidence": False,
        }
    )
    assert (
        run_carrier_qualification(candidate_payload, now_epoch=1001)["evidence"]["status"]
        == "insufficient"
    )


def test_failure_category_aggregate_is_bounded_and_private() -> None:
    sample = sample_probe_targets(candidate=candidate(), key=KEY, repository=REPO)
    categories = (
        "dns_failure",
        "connect_timeout",
        "connection_refused",
        "connect_failure",
        "tcp_connected",
    )
    outcomes = {
        target.identity: {
            "outcome": categories[index % len(categories)],
            "reachable": categories[index % len(categories)] == "tcp_connected",
            "latency_ms": 25.0 if categories[index % len(categories)] == "tcp_connected" else None,
        }
        for index, target in enumerate(sample.targets)
    }
    aggregate = build_carrier_probe_payload(
        carrier="telecom", sample=sample, outcomes=outcomes, collected_at_epoch=1000
    )
    row = aggregate["carriers"]["telecom"]
    assert set(row["outcomes"]) == set(categories)
    assert sum(row["outcomes"].values()) == row["tested"]
    assert row["outcomes"]["tcp_connected"] == row["reachable"]
    assert "example.invalid" not in json.dumps(aggregate)
    corrupted = json.loads(json.dumps(aggregate))
    corrupted["carriers"]["telecom"]["outcomes"]["dns_failure"] += 1
    with pytest.raises(ValidationError, match="outcome counts"):
        run_carrier_qualification(corrupted, now_epoch=1001)


def test_udp_native_skipped_and_producer_privacy() -> None:
    sample = sample_probe_targets(candidate=candidate(), key=KEY, repository=REPO)
    outcomes = {
        target.identity: {"reachable": False, "latency_ms": None, "outcome": "connection_refused"}
        for target in sample.targets
    }
    aggregate = build_carrier_probe_payload(
        carrier="telecom", sample=sample, outcomes=outcomes, collected_at_epoch=1000
    )
    row = aggregate["carriers"]["telecom"]
    assert row["skipped_unsupported"] == 4
    assert row["sampled"] == row["tested"] == 12
    serialized = json.dumps(aggregate)
    for forbidden in (
        "secret-name",
        "secret-credential",
        "example.invalid",
        "subscription_1",
        "hostname",
        "target",
        "raw_samples",
        "password",
    ):
        assert forbidden not in serialized
    assert set(aggregate) == {
        "schema_version",
        "collected_at_epoch",
        "sample_set_id",
        "inventory_set_id",
        "probe_plan_id",
        "sampler_version",
        "carriers",
    }


def test_runner_gate_rejects_github_hosted_and_wrong_carrier() -> None:
    env = {
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_LABELS": "self-hosted,carrier-probe-telecom",
        "CLASH_RELAY_CARRIER_PROBE_ENABLED": "true",
        "CLASH_RELAY_CARRIER_PROBE_NETWORK": "telecom",
    }
    with pytest.raises(ValidationError):
        assert_self_hosted_probe_environment(env, "telecom")
    env["RUNNER_ENVIRONMENT"] = "self-hosted"
    env.pop("RUNNER_LABELS")  # labels are selected by workflow, not attested here
    assert_self_hosted_probe_environment(env, "telecom")
    with pytest.raises(ValidationError):
        assert_self_hosted_probe_environment(env, "unicom")


def test_end_to_end_three_producers_and_collector(monkeypatch) -> None:
    monkeypatch.setattr(
        "clash_relay.carrier_probe.probe_tcp_endpoint",
        lambda **_kwargs: {"reachable": True, "latency_ms": 20.0, "outcome": "tcp_connected"},
    )
    outputs = []
    for carrier in ("telecom", "unicom", "mobile"):
        env = {
            "GITHUB_ACTIONS": "true",
            "RUNNER_ENVIRONMENT": "self-hosted",
            "RUNNER_LABELS": f"self-hosted,carrier-probe-{carrier}",
            "CLASH_RELAY_CARRIER_PROBE_ENABLED": "true",
            "CLASH_RELAY_CARRIER_PROBE_NETWORK": carrier,
        }
        outputs.append(
            run_carrier_probe(
                carrier=carrier, candidate=candidate(), key=KEY, repository=REPO, env=env
            )
        )
    merged = collect_carrier_probes(outputs)
    report = safe_carrier_report(
        run_carrier_qualification(merged, now_epoch=merged["collected_at_epoch"] + 1)
    )
    assert report["coverage"] == "full"
    assert report["evidence"]["status"] == "sufficient"
    assert report["aggregate"]["tested"] == 36
    assert report["aggregate"]["reachable"] == 36
    assert all(row["median_latency_ms"] == 20 for row in report["carriers"].values())


def test_tcp_dns_timeout_and_refused(monkeypatch) -> None:
    monkeypatch.setattr("clash_relay.carrier_probe._resolve_bounded", lambda *_: [])
    assert probe_tcp_endpoint(hostname="bad.invalid", port=443)["outcome"] == "dns_failure"
    monkeypatch.setattr(
        "clash_relay.carrier_probe._resolve_bounded",
        lambda *_: [(socket.AF_INET, None, None, None, ("1.1.1.1", 443))],
    )

    class RefusedSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def settimeout(self, _):
            return None

        def connect(self, _):
            raise ConnectionRefusedError()

    monkeypatch.setattr(socket, "socket", lambda *_: RefusedSocket())
    assert (
        probe_tcp_endpoint(hostname="target.invalid", port=443, attempts=1)["outcome"]
        == "connection_refused"
    )

    class TimeoutSocket(RefusedSocket):
        def connect(self, _):
            raise TimeoutError()

    monkeypatch.setattr(socket, "socket", lambda *_: TimeoutSocket())
    assert (
        probe_tcp_endpoint(hostname="target.invalid", port=443, attempts=1)["outcome"]
        == "connect_timeout"
    )


def test_workflow_keeps_probes_off_github_hosted_publish_path() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/carrier-probe.yml").read_text(encoding="utf-8")
    publish = (root / ".github/workflows/publish.yml").read_text(encoding="utf-8")
    for carrier in ("telecom", "unicom", "mobile"):
        assert f"runs-on: [self-hosted, linux, carrier-probe-{carrier}]" in workflow
    assert "upload-artifact" not in workflow
    assert "--production-preflight --carrier-qualification-input" in workflow
    assert "carrier_qualification_json" not in publish
    assert "--carrier-qualification-input" not in publish


def test_collector_cli_writes_only_merged_aggregate(tmp_path: Path) -> None:
    paths = []
    for carrier in ("telecom", "unicom", "mobile"):
        path = tmp_path / f"{carrier}.json"
        path.write_text(json.dumps(payload(carrier, timestamp=int(time.time()))), encoding="utf-8")
        paths.append(path)
    output = tmp_path / "carrier-qualification.json"
    script = Path(__file__).resolve().parents[1] / "scripts/collect_carrier_probes.py"
    command = [sys.executable, str(script)]
    for path in paths:
        command.extend(("--input", str(path)))
    command.extend(("--output", str(output)))
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    merged = json.loads(output.read_text(encoding="utf-8"))
    assert set(merged["carriers"]) == {"telecom", "unicom", "mobile"}
    assert "targets" not in output.read_text(encoding="utf-8")
