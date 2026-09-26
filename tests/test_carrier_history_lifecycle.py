from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from clash_relay.carrier_collector import collect_carrier_probes
from clash_relay.carrier_history import empty_history, observe_campaign
from clash_relay.carrier_probe import build_carrier_probe_payload, sample_probe_targets
from clash_relay.errors import ValidationError
from clash_relay.production_lifecycle import ProductionLifecyclePaths
from clash_relay.production_preflight import ProductionPreflightPipeline

KEY = b"carrier-history-repository-key-123456789"
REPOSITORY = "hzoonp/clash-relay"


def _candidate() -> dict:
    payload = []
    for region, source, offset in (("JP", "sub_1", 0), ("HK", "sub_2", 10)):
        for index in range(3):
            payload.append(
                {
                    "name": f"[BROWSING:{region}] {source}/fixture-{index} #0123456789",
                    "type": "http",
                    "server": f"private-{region}-{index}.fixture.invalid",
                    "port": 20000 + offset + index,
                    "password": "private-password",
                }
            )
    return {
        "proxy-providers": {"cr_browsing": {"payload": payload}},
        "proxy-groups": [],
        "rule-providers": {},
        "rules": [],
    }


def _aggregate(candidate: dict, now: int) -> dict:
    sample = sample_probe_targets(candidate=candidate, key=KEY, repository=REPOSITORY)
    outcomes = {
        target.identity: {"reachable": True, "latency_ms": 25.0, "outcome": "tcp_connected"}
        for target in sample.targets
    }
    return collect_carrier_probes(
        [
            build_carrier_probe_payload(
                carrier=carrier, sample=sample, outcomes=outcomes, collected_at_epoch=now
            )
            for carrier in ("telecom", "unicom", "mobile")
        ],
        now_epoch=now,
    )


def test_preflight_records_only_bound_aggregate_campaign(tmp_path: Path, monkeypatch) -> None:
    now = int(time.time())
    candidate = _candidate()
    source = tmp_path / "carrier-input.json"
    source.write_text(json.dumps(_aggregate(candidate, now)), encoding="utf-8")
    pipeline = ProductionPreflightPipeline(
        ProductionLifecyclePaths.canonical(tmp_path, carrier_qualification_input=source)
    )
    pipeline._prepare_dirs()
    pipeline._stage_carrier_input()
    assert pipeline._record_carrier_observation(SimpleNamespace()) == {
        "status": "skipped",
        "reason": "candidate_binding_not_verified",
    }
    assert not pipeline._private("carrier-observation-result.json").exists()
    monkeypatch.setenv("CLASH_RELAY_CARRIER_HMAC_KEY", KEY.decode())
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    generated = SimpleNamespace(config=candidate, yaml_text=yaml.safe_dump(candidate), report={})
    monkeypatch.setattr("clash_relay.production_lifecycle.build_candidate", lambda **_kw: generated)
    assert pipeline._generate()["status"] == "generated"
    captured = []

    def persist(*, project, report, env, binding_passed=True):
        assert binding_passed is True
        captured.append((project, report))
        assert env["CLASH_RELAY_CARRIER_HMAC_KEY"] == KEY.decode()
        history = observe_campaign(empty_history(), report, binding_passed=True, now_epoch=now)
        return {"status": "published", "history": history}

    monkeypatch.setattr("clash_relay.production_lifecycle.persist_carrier_observation", persist)
    project = SimpleNamespace()
    result = pipeline._record_carrier_observation(project)
    assert len(captured) == 1
    assert captured[0][1]["coverage"] == "full"
    assert captured[0][1]["freshness"]["status"] == "current"
    assert captured[0][1]["evidence"]["status"] == "sufficient"
    assert result["history"]["recent_campaign_count"] == 1
    assert result["history"]["carriers"]["telecom"]["campaign_runs"] == 1
    summary = pipeline._private("carrier-observation-summary.md").read_text(encoding="utf-8")
    assert "Recent campaigns: **1**" in summary
    for secret in (KEY.decode(), "fixture.invalid", "private-password", "sub_1/"):
        assert secret not in summary


def test_binding_failure_prevents_history_recording(tmp_path: Path, monkeypatch) -> None:
    now = int(time.time())
    source = tmp_path / "carrier-input.json"
    source.write_text(json.dumps(_aggregate(_candidate(), now)), encoding="utf-8")
    pipeline = ProductionPreflightPipeline(
        ProductionLifecyclePaths.canonical(tmp_path, carrier_qualification_input=source)
    )
    pipeline._prepare_dirs()
    pipeline._stage_carrier_input()
    monkeypatch.setenv("CLASH_RELAY_CARRIER_HMAC_KEY", KEY.decode())
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    changed = _candidate()
    changed["proxy-providers"]["cr_browsing"]["payload"][0]["server"] = "new.fixture.invalid"
    generated = SimpleNamespace(config=changed, yaml_text=yaml.safe_dump(changed), report={})
    monkeypatch.setattr("clash_relay.production_lifecycle.build_candidate", lambda **_kw: generated)
    called = []
    monkeypatch.setattr(
        "clash_relay.production_lifecycle.persist_carrier_observation",
        lambda **kwargs: called.append(kwargs),
    )
    with pytest.raises(ValidationError, match="current candidate"):
        pipeline._generate()
    assert called == []
    assert not pipeline._private("carrier-observation-result.json").exists()


def test_preflight_without_carrier_input_skips_history(tmp_path: Path, monkeypatch) -> None:
    pipeline = ProductionPreflightPipeline(ProductionLifecyclePaths.canonical(tmp_path))
    monkeypatch.delenv("CLASH_RELAY_CARRIER_HMAC_KEY", raising=False)
    called = []
    monkeypatch.setattr(
        "clash_relay.production_lifecycle.persist_carrier_observation",
        lambda **kwargs: called.append(kwargs),
    )
    assert pipeline._record_carrier_observation(SimpleNamespace()) == {"status": "not_configured"}
    assert called == []
