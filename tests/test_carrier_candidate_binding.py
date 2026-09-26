"""A real generated inventory must match the private carrier probe plan."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import pytest
import yaml

from clash_relay.builder import build_candidate
from clash_relay.carrier_collector import collect_carrier_probes
from clash_relay.carrier_probe import (
    build_carrier_probe_payload,
    sample_probe_targets,
    verify_carrier_candidate_binding,
)
from clash_relay.carrier_qualification import run_carrier_qualification
from clash_relay.errors import ValidationError
from clash_relay.mihomo import load_candidate
from clash_relay.production_lifecycle import ProductionLifecyclePaths
from clash_relay.production_pipeline import (
    ProductionPipelineOutputs,
    ProjectPaths,
    QualificationPaths,
    run_production_pipeline,
)
from clash_relay.production_preflight import ProductionPreflightPipeline
from clash_relay.qualification_pipeline import run_qualification_pipeline

KEY = b"candidate-binding-repository-secret-123456789"
OTHER_KEY = b"candidate-binding-other-repository-secret-123"
REPOSITORY = "hzoonp/clash-relay"


def _candidate(repo_root: Path, *, changed_subscription: bool = False):
    env = {f"SUBSCRIPTION_{i}_URL": f"https://fixture.invalid/{i}" for i in range(1, 6)}

    def subscription(url: str, **_kwargs) -> str:
        source = int(url.rsplit("/", 1)[1])
        proxies = []
        for region, base in (("JP", 20000), ("HK", 21000)):
            for index in range(6):
                hostname = f"{region.lower()}-{source}-{index}.fixture.invalid"
                if changed_subscription and source == 2 and region == "JP" and index == 5:
                    hostname = "replacement-private-endpoint.fixture.invalid"
                proxies.append(
                    {
                        "name": f"{region} Node {source}-{index}",
                        "type": "http",
                        "server": hostname,
                        "port": base + source * 10 + index,
                        "password": f"private-password-{source}-{index}",
                    }
                )
        return yaml.safe_dump({"proxies": proxies}, sort_keys=False)

    return build_candidate(
        config_path=repo_root / "config.yaml",
        subscriptions_path=repo_root / "subscriptions.yaml",
        policies_path=repo_root / "policies.yaml",
        env=env,
        fetcher=subscription,
        rule_fetcher=lambda _url, **_kwargs: "DOMAIN-SUFFIX,fixture.invalid\n",
    )


def _aggregate(candidate: dict) -> dict:
    sample = sample_probe_targets(candidate=candidate, key=KEY, repository=REPOSITORY)
    outcomes = {
        target.identity: {"reachable": True, "latency_ms": 20.0, "outcome": "tcp_connected"}
        for target in sample.targets
    }
    now = int(time.time())
    return collect_carrier_probes(
        [
            build_carrier_probe_payload(
                carrier=carrier,
                sample=sample,
                outcomes=outcomes,
                collected_at_epoch=now,
            )
            for carrier in ("telecom", "unicom", "mobile")
        ],
        now_epoch=now,
    )


@pytest.fixture(scope="module")
def candidate_a(repo_root: Path) -> dict:
    return _candidate(repo_root).config


@pytest.fixture(scope="module")
def aggregate_a(candidate_a: dict) -> dict:
    return _aggregate(candidate_a)


def test_generated_producer_collector_and_candidate_a_are_bound(
    candidate_a: dict, aggregate_a: dict
) -> None:
    verify_carrier_candidate_binding(candidate_a, aggregate_a, key=KEY, repository=REPOSITORY)
    assert run_carrier_qualification(aggregate_a)["evidence"]["status"] == "sufficient"
    sample = sample_probe_targets(candidate=candidate_a, key=KEY, repository=REPOSITORY)
    assert sample == sample_probe_targets(candidate=candidate_a, key=KEY, repository=REPOSITORY)
    assert aggregate_a["sample_set_id"] == sample.sample_set_id
    assert aggregate_a["inventory_set_id"] == sample.inventory_set_id
    assert aggregate_a["probe_plan_id"] == sample.probe_plan_id
    assert aggregate_a["sampler_version"] == sample.sampler_version

    reordered = copy.deepcopy(candidate_a)
    reordered["proxy-providers"] = dict(reversed(list(reordered["proxy-providers"].items())))
    for provider in reordered["proxy-providers"].values():
        provider["payload"].reverse()
    reordered_sample = sample_probe_targets(candidate=reordered, key=KEY, repository=REPOSITORY)
    assert reordered_sample.sample_set_id == sample.sample_set_id
    assert reordered_sample.inventory_set_id == sample.inventory_set_id
    assert reordered_sample.probe_plan_id == sample.probe_plan_id


def test_serialized_generated_candidate_has_identical_probe_plan(
    repo_root: Path, tmp_path: Path, aggregate_a: dict
) -> None:
    serialized = tmp_path / "generated.yaml"
    serialized.write_text(_candidate(repo_root).yaml_text, encoding="utf-8")
    verify_carrier_candidate_binding(
        load_candidate(serialized), aggregate_a, key=KEY, repository=REPOSITORY
    )


def test_new_subscription_inventory_rejects_old_carrier_evidence(
    repo_root: Path, aggregate_a: dict
) -> None:
    candidate_b = _candidate(repo_root, changed_subscription=True).config
    with pytest.raises(ValidationError, match=r"inventory|sample|plan|candidate"):
        verify_carrier_candidate_binding(candidate_b, aggregate_a, key=KEY, repository=REPOSITORY)


def test_unselected_inventory_drift_is_rejected(candidate_a: dict, aggregate_a: dict) -> None:
    changed = copy.deepcopy(candidate_a)
    sampled = sample_probe_targets(candidate=candidate_a, key=KEY, repository=REPOSITORY)
    selected = {(target.hostname, target.port) for target in sampled.targets}
    for provider in changed["proxy-providers"].values():
        for proxy in provider["payload"]:
            if (proxy["server"], proxy["port"]) not in selected:
                proxy["server"] = "changed-unselected-private.fixture.invalid"
                with pytest.raises(ValidationError, match="candidate"):
                    verify_carrier_candidate_binding(
                        changed, aggregate_a, key=KEY, repository=REPOSITORY
                    )
                return
    raise AssertionError("fixture lacks an unselected endpoint")


def test_repository_hmac_key_mismatch_is_rejected(candidate_a: dict, aggregate_a: dict) -> None:
    with pytest.raises(ValidationError, match=r"inventory|sample|plan|candidate"):
        verify_carrier_candidate_binding(
            candidate_a, aggregate_a, key=OTHER_KEY, repository=REPOSITORY
        )
    with pytest.raises(ValidationError, match=r"inventory|sample|plan|candidate"):
        verify_carrier_candidate_binding(candidate_a, aggregate_a, key=KEY, repository="other/repo")


def test_sampler_version_mismatch_is_rejected(candidate_a: dict, aggregate_a: dict) -> None:
    changed = {**aggregate_a, "sampler_version": aggregate_a["sampler_version"] + 1}
    with pytest.raises(ValidationError, match="sampler"):
        verify_carrier_candidate_binding(candidate_a, changed, key=KEY, repository=REPOSITORY)


def test_regional_topology_drift_rejects_old_probe_plan(
    candidate_a: dict, aggregate_a: dict
) -> None:
    changed = copy.deepcopy(candidate_a)
    changed_count = 0
    for provider in changed["proxy-providers"].values():
        for proxy in provider["payload"]:
            if proxy["name"].startswith("[BROWSING:JP]"):
                proxy["name"] = proxy["name"].replace("[BROWSING:JP]", "[BROWSING:SG]")
                changed_count += 1
    assert changed_count > 0
    with pytest.raises(ValidationError, match=r"inventory|plan|candidate|geographic"):
        verify_carrier_candidate_binding(changed, aggregate_a, key=KEY, repository=REPOSITORY)


def test_policy_only_drift_rejects_old_probe_plan(candidate_a: dict, aggregate_a: dict) -> None:
    changed = copy.deepcopy(candidate_a)
    changed["rules"] = [*changed["rules"], "DOMAIN,policy-drift.fixture.invalid,DIRECT"]
    original = sample_probe_targets(candidate=candidate_a, key=KEY, repository=REPOSITORY)
    modified = sample_probe_targets(candidate=changed, key=KEY, repository=REPOSITORY)
    assert modified.sample_set_id == original.sample_set_id
    assert modified.inventory_set_id == original.inventory_set_id
    assert modified.probe_plan_id != original.probe_plan_id
    with pytest.raises(ValidationError, match="current candidate"):
        verify_carrier_candidate_binding(changed, aggregate_a, key=KEY, repository=REPOSITORY)


def test_aggregate_has_no_private_endpoint_material(candidate_a: dict, aggregate_a: dict) -> None:
    serialized = json.dumps(aggregate_a, sort_keys=True)
    assert set(aggregate_a) == {
        "schema_version",
        "collected_at_epoch",
        "sample_set_id",
        "inventory_set_id",
        "probe_plan_id",
        "sampler_version",
        "carriers",
    }
    for secret in (
        "fixture.invalid",
        "private-password",
        KEY.decode(),
        "SUBSCRIPTION_",
        "hostname",
        "server",
        '"port":',
        "raw_samples",
        "targets",
        "fingerprint",
    ):
        assert secret not in serialized
    assert candidate_a["proxy-providers"]


def test_preflight_generation_accepts_exact_candidate_and_rejects_drift(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, aggregate_a: dict
) -> None:
    """Exercise the canonical preflight generation hook without external network calls."""
    source = tmp_path / "carrier-qualification.json"
    source.write_text(json.dumps(aggregate_a), encoding="utf-8")
    pipeline = ProductionPreflightPipeline(
        ProductionLifecyclePaths.canonical(tmp_path, carrier_qualification_input=source)
    )
    pipeline._prepare_dirs()
    pipeline._stage_carrier_input()
    monkeypatch.setenv("CLASH_RELAY_CARRIER_HMAC_KEY", KEY.decode())
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    exact_result = _candidate(repo_root)
    monkeypatch.setattr(
        "clash_relay.production_lifecycle.build_candidate", lambda **_kw: exact_result
    )
    assert pipeline._generate()["status"] == "generated"
    for path in pipeline.paths.private_dir.iterdir():
        if path.is_file():
            assert KEY.decode() not in path.read_text(encoding="utf-8")
    drifted_result = _candidate(repo_root, changed_subscription=True)
    monkeypatch.setattr(
        "clash_relay.production_lifecycle.build_candidate", lambda **_kw: drifted_result
    )
    with pytest.raises(ValidationError, match=r"inventory|sample|plan|candidate"):
        pipeline._generate()


def test_preflight_with_evidence_requires_hmac_key(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, aggregate_a: dict
) -> None:
    source = tmp_path / "carrier-qualification.json"
    source.write_text(json.dumps(aggregate_a), encoding="utf-8")
    pipeline = ProductionPreflightPipeline(
        ProductionLifecyclePaths.canonical(tmp_path, carrier_qualification_input=source)
    )
    pipeline._prepare_dirs()
    pipeline._stage_carrier_input()
    monkeypatch.delenv("CLASH_RELAY_CARRIER_HMAC_KEY", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    generated = _candidate(repo_root)
    monkeypatch.setattr("clash_relay.production_lifecycle.build_candidate", lambda **_kw: generated)
    with pytest.raises(ValidationError, match=r"HMAC|key|carrier"):
        pipeline._generate()


def test_preflight_without_carrier_input_needs_no_hmac_key(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline = ProductionPreflightPipeline(ProductionLifecyclePaths.canonical(tmp_path))
    pipeline._prepare_dirs()
    monkeypatch.delenv("CLASH_RELAY_CARRIER_HMAC_KEY", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    generated = _candidate(repo_root)
    monkeypatch.setattr("clash_relay.production_lifecycle.build_candidate", lambda **_kw: generated)
    assert pipeline._generate()["status"] == "generated"


def test_standalone_production_pipeline_rejects_old_candidate_evidence(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, aggregate_a: dict
) -> None:
    """The lower-level CLI/application entrypoint cannot bypass binding."""
    candidate_path = tmp_path / "generated.yaml"
    candidate_path.write_text(
        _candidate(repo_root, changed_subscription=True).yaml_text, encoding="utf-8"
    )
    evidence_path = tmp_path / "carrier.json"
    evidence_path.write_text(json.dumps(aggregate_a), encoding="utf-8")
    monkeypatch.setenv("CLASH_RELAY_CARRIER_HMAC_KEY", KEY.decode())
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    with pytest.raises(ValidationError, match="current candidate"):
        run_production_pipeline(
            project_paths=ProjectPaths(
                config=repo_root / "config.yaml",
                subscriptions=repo_root / "subscriptions.yaml",
                policies=repo_root / "policies.yaml",
            ),
            qualification_paths=QualificationPaths(
                candidate=candidate_path,
                output=tmp_path / "qualified.yaml",
                mihomo_bin=tmp_path / "mihomo",
                stage_dir=tmp_path / "stages",
                browsing_report=tmp_path / "browsing.json",
                ai_report=tmp_path / "ai.json",
                carrier_input=evidence_path,
            ),
            outputs=ProductionPipelineOutputs(
                pre_audit=tmp_path / "pre.json",
                post_audit=tmp_path / "post.json",
                qualification=tmp_path / "qualification.json",
            ),
        )
    assert not (tmp_path / "pre.json").exists()


def test_direct_qualification_entrypoint_rejects_old_evidence(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, aggregate_a: dict
) -> None:
    candidate_path = tmp_path / "generated.yaml"
    candidate_path.write_text(
        _candidate(repo_root, changed_subscription=True).yaml_text, encoding="utf-8"
    )
    evidence_path = tmp_path / "carrier.json"
    evidence_path.write_text(json.dumps(aggregate_a), encoding="utf-8")
    monkeypatch.setenv("CLASH_RELAY_CARRIER_HMAC_KEY", KEY.decode())
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    output = tmp_path / "qualified.yaml"
    with pytest.raises(ValidationError, match="current candidate"):
        run_qualification_pipeline(
            candidate=candidate_path,
            output=output,
            policies=repo_root / "policies.yaml",
            mihomo_bin=tmp_path / "missing-mihomo",
            stage_dir=tmp_path / "stages",
            browsing_report=tmp_path / "browsing.json",
            ai_report=tmp_path / "ai.json",
            carrier_input=evidence_path,
        )
    assert not output.exists()
