"""Carrier sampling against the actual production generator's runtime graph."""

from __future__ import annotations

import copy
import json
import time
from collections import defaultdict
from pathlib import Path

import pytest
import yaml

from clash_relay.builder import build_candidate
from clash_relay.carrier_collector import collect_carrier_probes
from clash_relay.carrier_probe import build_carrier_probe_payload, sample_probe_targets
from clash_relay.carrier_qualification import run_carrier_qualification, safe_carrier_report
from clash_relay.errors import ValidationError
from clash_relay.production_lifecycle import ProductionLifecyclePaths
from clash_relay.production_preflight import ProductionPreflightPipeline
from clash_relay.qualification_pipeline import _carrier_report
from clash_relay.runtime_names import parse_runtime_name_region, parse_runtime_source_name

KEY = b"one-repository-only-secret-key-value-123456"
REPOSITORY = "hzoonp/clash-relay"
GEOGRAPHIC = {"HK", "TW", "SG", "JP", "US", "KR", "OTHER"}


@pytest.fixture(scope="module")
def generated_candidate(repo_root: Path) -> dict:
    """Use real source policy, classification, deduplication, and provider names."""
    env = {
        f"SUBSCRIPTION_{index}_URL": f"https://fixture.invalid/sub/{index}" for index in range(1, 6)
    }

    def subscription(url: str, **_kwargs) -> str:
        source = int(url.rsplit("/", 1)[1])
        proxies = [
            {
                "name": f"JP Node {source}-{index}",
                "type": "http",
                "server": f"jp-{source}-{index}.fixture.invalid",
                "port": 20000 + source * 10 + index,
                "password": f"private-password-{source}-{index}",
            }
            for index in range(6)
        ]
        proxies.extend(
            {
                "name": f"HK Node {source}-{index}",
                "type": "http",
                "server": f"hk-{source}-{index}.fixture.invalid",
                "port": 21000 + source * 10 + index,
                "password": f"private-password-{source}-{index}",
            }
            for index in range(6)
        )
        return yaml.safe_dump({"proxies": proxies}, sort_keys=False)

    result = build_candidate(
        config_path=repo_root / "config.yaml",
        subscriptions_path=repo_root / "subscriptions.yaml",
        policies_path=repo_root / "policies.yaml",
        env=env,
        fetcher=subscription,
        rule_fetcher=lambda _url, **_kwargs: "DOMAIN-SUFFIX,fixture.invalid\n",
    )
    return result.config


def _occurrences(candidate: dict) -> dict[tuple[str, int], set[str]]:
    by_endpoint: dict[tuple[str, int], set[str]] = defaultdict(set)
    for provider in candidate["proxy-providers"].values():
        for proxy in provider["payload"]:
            by_endpoint[(proxy["server"], proxy["port"])].add(proxy["name"])
    return by_endpoint


def _sample(candidate: dict, *, max_targets: int = 12):
    return sample_probe_targets(
        candidate=candidate,
        key=KEY,
        repository=REPOSITORY,
        max_targets=max_targets,
    )


def test_generated_general_copies_do_not_override_regional_identity(
    generated_candidate: dict,
) -> None:
    occurrences = _occurrences(generated_candidate)
    assert any(
        any(name.startswith("[GENERAL:ANY]") for name in names)
        and any(name.startswith("[BROWSING:JP]") for name in names)
        for names in occurrences.values()
    )
    assert any(
        any(name.startswith("[GENERAL:ANY]") for name in names)
        and any(name.startswith("[BROWSING:HK]") for name in names)
        for names in occurrences.values()
    )

    sample = _sample(generated_candidate)
    assert sample == _sample(generated_candidate)
    assert sample.sampled == 12
    assert {target.region for target in sample.targets} == {"HK", "JP"}
    assert {target.source for target in sample.targets} >= {"sub_2", "sub_3"}
    assert sample.sampler_version == 3
    assert sample.inventory_set_id != sample.sample_set_id
    assert sample.probe_plan_id

    for target in sample.targets:
        names = occurrences[(target.hostname, target.port)]
        geographic = {
            region for name in names if (region := parse_runtime_name_region(name)) in GEOGRAPHIC
        }
        assert geographic == {target.region}
        assert target.source in {parse_runtime_source_name(name) for name in names}
        assert all(name.startswith("[") for name in names)


def test_generated_inventory_identity_detects_unselected_endpoint_drift(
    generated_candidate: dict,
) -> None:
    original = _sample(generated_candidate, max_targets=1)
    selected = {(target.hostname, target.port) for target in original.targets}
    changed = copy.deepcopy(generated_candidate)
    for provider in changed["proxy-providers"].values():
        for proxy in provider["payload"]:
            if (proxy["server"], proxy["port"]) not in selected:
                proxy["server"] = "changed-unselected-endpoint.fixture.invalid"
                break
        else:
            continue
        break
    modified = _sample(changed, max_targets=1)
    assert modified.sample_set_id == original.sample_set_id
    assert modified.inventory_set_id != original.inventory_set_id
    payloads = [
        build_carrier_probe_payload(
            carrier=carrier,
            sample=sample,
            outcomes={
                sample.targets[0].identity: {
                    "reachable": False,
                    "latency_ms": None,
                    "outcome": "connection_refused",
                }
            },
            collected_at_epoch=1000,
        )
        for carrier, sample in (
            ("telecom", original),
            ("unicom", original),
            ("mobile", modified),
        )
    ]
    with pytest.raises(ValidationError, match="inventor"):
        collect_carrier_probes(payloads, now_epoch=1001)


def test_inventory_identity_detects_unselected_proxy_material_drift(
    generated_candidate: dict,
) -> None:
    original = _sample(generated_candidate, max_targets=1)
    selected = {(target.hostname, target.port) for target in original.targets}
    changed = copy.deepcopy(generated_candidate)
    for provider in changed["proxy-providers"].values():
        for proxy in provider["payload"]:
            if (proxy["server"], proxy["port"]) not in selected:
                proxy["password"] = "different-private-password"
                modified = _sample(changed, max_targets=1)
                assert modified.sample_set_id == original.sample_set_id
                assert modified.inventory_set_id != original.inventory_set_id
                return
    raise AssertionError("fixture must contain an unselected endpoint")


def test_generated_conflicting_geographic_occurrence_fails_closed(
    generated_candidate: dict,
) -> None:
    changed = copy.deepcopy(generated_candidate)
    provider = next(
        provider
        for provider in changed["proxy-providers"].values()
        if any(proxy["name"].startswith("[BROWSING:JP]") for proxy in provider["payload"])
    )
    proxy = next(
        proxy for proxy in provider["payload"] if proxy["name"].startswith("[BROWSING:JP]")
    )
    conflict = dict(proxy)
    conflict["name"] = conflict["name"].replace("[BROWSING:JP]", "[BROWSING:HK]")
    provider["payload"].append(conflict)
    with pytest.raises(ValidationError, match=r"geographic|region|conflict"):
        _sample(changed)


def test_generated_non_geographic_scopes_remain_unknown(generated_candidate: dict) -> None:
    changed = copy.deepcopy(generated_candidate)
    for provider in changed["proxy-providers"].values():
        provider["payload"] = [
            proxy for proxy in provider["payload"] if proxy["name"].startswith("[GENERAL:ANY]")
        ]
    sample = _sample(changed)
    assert sample.targets
    assert {target.region for target in sample.targets} == {"UNKNOWN"}
    first = next(
        proxy for provider in changed["proxy-providers"].values() for proxy in provider["payload"]
    )
    chained = dict(first)
    chained["name"] = chained["name"].replace("[GENERAL:ANY]", "[CHAIN_ENTRY:CHAIN]")
    next(iter(changed["proxy-providers"].values()))["payload"].append(chained)
    assert {target.region for target in _sample(changed).targets} == {"UNKNOWN"}


def test_generated_tcp_and_udp_shared_endpoint_is_not_skipped(
    generated_candidate: dict,
) -> None:
    original = _sample(generated_candidate)
    changed = copy.deepcopy(generated_candidate)
    target = original.targets[0]
    provider = next(iter(changed["proxy-providers"].values()))
    provider["payload"].append(
        {
            "name": "[BROWSING:JP] sub_2/udp-copy #0123456789",
            "type": "hysteria2",
            "server": target.hostname,
            "port": target.port,
        }
    )
    mixed = _sample(changed)
    assert mixed.sample_set_id == original.sample_set_id
    assert mixed.skipped_udp_native_endpoints == original.skipped_udp_native_endpoints
    assert mixed.sampled_tcp_endpoints == original.sampled_tcp_endpoints


def test_generated_three_carrier_aggregate_stays_private_and_advisory(
    generated_candidate: dict,
) -> None:
    sample = _sample(generated_candidate)
    outcomes = {
        target.identity: {"reachable": True, "latency_ms": 25.0, "outcome": "tcp_connected"}
        for target in sample.targets
    }
    payloads = [
        build_carrier_probe_payload(
            carrier=carrier,
            sample=sample,
            outcomes=outcomes,
            collected_at_epoch=1000,
        )
        for carrier in ("telecom", "unicom", "mobile")
    ]
    merged = collect_carrier_probes(payloads, now_epoch=1001)
    report = safe_carrier_report(run_carrier_qualification(merged, now_epoch=1001))
    assert report["authority"] == "external_self_hosted_advisory"
    assert report["coverage"] == "full"
    assert report["evidence"]["status"] == "sufficient"
    assert report["aggregate"]["tested"] == 36
    assert report["aggregate"]["reachable"] == 36
    assert all(row["geographic_regions_sampled"] == 2 for row in report["carriers"].values())
    assert all(row["sources_sampled"] >= 2 for row in report["carriers"].values())
    serialized = json.dumps({"payloads": payloads, "merged": merged, "report": report})
    for private in (
        "fixture.invalid",
        "private-password",
        "subscription_",
        "sub_2/",
        "targets",
        "hostname",
        "raw_samples",
    ):
        assert private not in serialized


def test_single_generated_stratum_uses_bounded_full_inventory_fallback(
    generated_candidate: dict,
) -> None:
    single_stratum = copy.deepcopy(generated_candidate)
    for provider in single_stratum["proxy-providers"].values():
        provider["payload"] = [
            proxy
            for proxy in provider["payload"]
            if proxy["name"].startswith("[BROWSING:JP] sub_2/")
        ]
    sample = _sample(single_stratum)
    assert sample.sampled >= 5
    payload = build_carrier_probe_payload(
        carrier="telecom",
        sample=sample,
        outcomes={
            target.identity: {"reachable": False, "latency_ms": None, "outcome": "dns_failure"}
            for target in sample.targets
        },
        collected_at_epoch=1000,
    )
    row = payload["carriers"]["telecom"]
    assert row["geographic_regions_sampled"] == 1
    assert row["protocols_sampled"] == 1
    assert row["sources_sampled"] == 1
    assert row["strata_sampled"] == 1
    assert row["eligible_tcp_endpoints"] == sample.sampled
    assert row["sufficient_evidence"] is True
    report = run_carrier_qualification(payload, now_epoch=1001)
    assert report["evidence"]["status"] == "sufficient"


def test_generated_producers_collector_and_canonical_preflight_ingestion(
    generated_candidate: dict, tmp_path: Path
) -> None:
    sample = _sample(generated_candidate)
    outcomes = {
        target.identity: {"reachable": True, "latency_ms": 25.0, "outcome": "tcp_connected"}
        for target in sample.targets
    }
    now = int(time.time())
    merged = collect_carrier_probes(
        [
            build_carrier_probe_payload(
                carrier=carrier, sample=sample, outcomes=outcomes, collected_at_epoch=now
            )
            for carrier in ("telecom", "unicom", "mobile")
        ],
        now_epoch=now,
    )
    source = tmp_path / "carrier-qualification.json"
    source.write_text(json.dumps(merged), encoding="utf-8")
    preflight = ProductionPreflightPipeline(
        ProductionLifecyclePaths.canonical(tmp_path, carrier_qualification_input=source)
    )
    preflight._prepare_dirs()
    preflight._stage_carrier_input()
    report = _carrier_report(preflight._carrier_input_snapshot)
    assert report["coverage"] == "full"
    assert report["evidence"]["status"] == "sufficient"
    assert report["sample_set_id"] == sample.sample_set_id
    assert report["inventory_set_id"] == sample.inventory_set_id
