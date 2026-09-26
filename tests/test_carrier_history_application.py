from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from clash_relay.carrier_history_application import (
    persist_carrier_observation,
    render_carrier_history_markdown,
)
from clash_relay.carrier_qualification import run_carrier_qualification
from clash_relay.errors import PublicationError

ENV = {
    "CLOUDFLARE_API_TOKEN": "private-token",
    "CLOUDFLARE_ACCOUNT_ID": "account",
    "CLOUDFLARE_KV_NAMESPACE_TITLE": "namespace",
}


def _report(
    *, collected: int, now: int, carriers=("telecom", "unicom", "mobile"), reachable: int = 4
):
    row = {
        "sampled": 5,
        "sampled_tcp_endpoints": 5,
        "tested": 5,
        "reachable": reachable,
        "skipped_unsupported": 0,
        "skipped_udp_native_endpoints": 0,
        "geographic_regions_sampled": 2,
        "protocols_sampled": 1,
        "sources_sampled": 1,
        "strata_sampled": 2,
        "eligible_tcp_endpoints": 5,
        "sufficient_evidence": True,
        "median_latency_ms": 42.0 if reachable else None,
        "p90_latency_ms": 55.0 if reachable else None,
        "outcomes": {
            "dns_failure": 5 - reachable,
            "connect_timeout": 0,
            "connection_refused": 0,
            "connect_failure": 0,
            "tcp_connected": reachable,
        },
    }
    return run_carrier_qualification(
        {
            "schema_version": 1,
            "collected_at_epoch": collected,
            "carriers": dict.fromkeys(carriers, row),
        },
        now_epoch=now,
    )


@pytest.fixture
def memory_kv(monkeypatch):
    storage: dict[str, bytes] = {}
    calls: list[str] = []

    class Publisher:
        def __init__(self, *, token, account_id, namespace_title, key_name):
            assert token == ENV["CLOUDFLARE_API_TOKEN"]
            assert account_id == ENV["CLOUDFLARE_ACCOUNT_ID"]
            assert namespace_title == ENV["CLOUDFLARE_KV_NAMESPACE_TITLE"]
            assert key_name == "config.carrier-observation-history-v1"
            self.key_name = key_name

        def read(self):
            calls.append("read")
            return storage.get(self.key_name)

        def publish(self, *, content):
            calls.append("publish")
            storage[self.key_name] = content
            return {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}

    monkeypatch.setattr("clash_relay.carrier_history_application.CloudflareKVPublisher", Publisher)
    return storage, calls


def _project():
    return SimpleNamespace(config={"publishing": {"cloudflare_kv": {"key": "config"}}})


def test_valid_history_persists_in_separate_aggregate_key_and_retry_is_idempotent(memory_kv):
    storage, calls = memory_kv
    report = _report(collected=999, now=1000)
    first = persist_carrier_observation(project=_project(), report=report, env=ENV, now_epoch=1000)
    assert first["status"] == "published"
    assert first["history"]["recent_campaign_count"] == 1
    assert first["history"]["consecutive_valid_campaigns"] == 1
    assert first["history"]["carriers"]["telecom"]["reachable_ratio_ema"] == 0.8
    assert calls == ["read", "publish"]
    repeated = persist_carrier_observation(
        project=_project(), report=report, env=ENV, now_epoch=1010
    )
    assert repeated["status"] == "unchanged"
    assert calls == ["read", "publish", "read"]
    persisted = storage["config.carrier-observation-history-v1"]
    for forbidden in (
        b"hostname",
        b"server",
        b"endpoint",
        b"source",
        b"password",
        b"sample_set_id",
        b"probe_plan_id",
        b"private-token",
    ):
        assert forbidden not in persisted
    markdown = render_carrier_history_markdown(first)
    assert "Recent campaigns" in markdown
    assert "saturating caps" in markdown
    for verdict in ("improving", "stable", "degrading"):
        assert verdict not in markdown


def test_stale_and_partial_campaigns_do_not_change_quality(memory_kv):
    _, _calls = memory_kv
    first = persist_carrier_observation(
        project=_project(), report=_report(collected=999, now=1000), env=ENV, now_epoch=1000
    )["history"]
    stale = persist_carrier_observation(
        project=_project(),
        report=_report(collected=1100, now=1100 + 7 * 3600),
        env=ENV,
        now_epoch=1100 + 7 * 3600,
    )["history"]
    assert stale["carriers"] == first["carriers"]
    assert stale["status_counts_lifetime"]["stale"] == 1
    assert stale["consecutive_valid_campaigns"] == 0
    partial = persist_carrier_observation(
        project=_project(),
        report=_report(
            collected=1100 + 7 * 3600 + 1, now=1100 + 7 * 3600 + 1, carriers=("telecom",)
        ),
        env=ENV,
        now_epoch=1100 + 7 * 3600 + 1,
    )["history"]
    assert partial["carriers"] == first["carriers"]
    assert partial["status_counts_lifetime"]["partial"] == 1


def test_zero_reachable_campaign_does_not_fake_latency(memory_kv):
    first = persist_carrier_observation(
        project=_project(), report=_report(collected=999, now=1000), env=ENV, now_epoch=1000
    )["history"]
    dead = persist_carrier_observation(
        project=_project(),
        report=_report(collected=1100, now=1100, reachable=0),
        env=ENV,
        now_epoch=1100,
    )["history"]
    row = dead["carriers"]["telecom"]
    assert row["median_latency_ms_ema"] == first["carriers"]["telecom"]["median_latency_ms_ema"]
    assert row["latency_sample_runs"] == 1
    assert row["last_latency_epoch"] == 999
    assert row["last_seen_epoch"] == 1100
    assert row["reachable_ratio_ema"] < first["carriers"]["telecom"]["reachable_ratio_ema"]


def test_malformed_previous_state_recovers_without_identity_leak(memory_kv):
    storage, _ = memory_kv
    storage["config.carrier-observation-history-v1"] = b'{"raw_samples":["198.51.100.1"]}'
    result = persist_carrier_observation(
        project=_project(), report=_report(collected=999, now=1000), env=ENV, now_epoch=1000
    )
    assert result["status"] == "published"
    assert result["history"]["recent_campaign_count"] == 1
    assert b"198.51.100.1" not in storage["config.carrier-observation-history-v1"]
    assert "raw_samples" not in json.dumps(result)


def test_missing_cloudflare_credentials_do_not_write_or_change_quality(memory_kv):
    _storage, calls = memory_kv
    result = persist_carrier_observation(
        project=_project(), report=_report(collected=999, now=1000), env={}, now_epoch=1000
    )
    assert result == {"status": "skipped", "reason": "cloudflare_unavailable"}
    assert calls == []


def test_read_failure_does_not_overwrite_history(monkeypatch):
    calls = []

    class Publisher:
        def __init__(self, **_kwargs):
            pass

        def read(self):
            raise PublicationError("unavailable")

        def publish(self, *, content):
            calls.append(content)

    monkeypatch.setattr("clash_relay.carrier_history_application.CloudflareKVPublisher", Publisher)
    result = persist_carrier_observation(
        project=_project(), report=_report(collected=999, now=1000), env=ENV, now_epoch=1000
    )
    assert result == {"status": "unavailable", "reason": "history_read_failed"}
    assert calls == []
