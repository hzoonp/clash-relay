"""Receipt-boundary tests: prepare/commit separation and fail-closed checks."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from clash_relay.carrier_observation_receipt import (
    RECEIPT_KEYS,
    build_carrier_observation_receipt,
    commit_carrier_observation_receipt,
    parse_carrier_observation_receipt,
)
from clash_relay.errors import ValidationError

SHA = "a" * 40
KEY = "repository-bound-carrier-receipt-key-123456"
ENV = {
    "CLOUDFLARE_API_TOKEN": "private-token",
    "CLOUDFLARE_ACCOUNT_ID": "account",
    "CLOUDFLARE_KV_NAMESPACE_TITLE": "namespace",
    "CLASH_RELAY_CARRIER_HMAC_KEY": KEY,
    "GITHUB_REPOSITORY": "hzoonp/clash-relay",
}


def _aggregate(*, collected: int = 1000, reachable: int = 6) -> dict:
    row = {
        "sampled": 6,
        "sampled_tcp_endpoints": 6,
        "tested": 6,
        "reachable": reachable,
        "skipped_unsupported": 1,
        "skipped_udp_native_endpoints": 1,
        "geographic_regions_sampled": 2,
        "protocols_sampled": 1,
        "sources_sampled": 1,
        "strata_sampled": 2,
        "eligible_tcp_endpoints": 6,
        "sufficient_evidence": True,
        "median_latency_ms": 40.0 if reachable else None,
        "p90_latency_ms": 55.0 if reachable else None,
        "outcomes": {
            "dns_failure": 6 - reachable,
            "connect_timeout": 0,
            "connection_refused": 0,
            "connect_failure": 0,
            "tcp_connected": reachable,
        },
    }
    return {
        "schema_version": 1,
        "collected_at_epoch": collected,
        "sample_set_id": "a" * 32,
        "inventory_set_id": "b" * 32,
        "probe_plan_id": "c" * 32,
        "sampler_version": 3,
        "carriers": dict.fromkeys(("telecom", "unicom", "mobile"), row),
    }


def _receipt() -> dict:
    return build_carrier_observation_receipt(aggregate=_aggregate(), validated_sha=SHA, env=ENV)


def test_receipt_shape_is_strict_and_aggregate_only() -> None:
    receipt = _receipt()
    assert set(receipt) == RECEIPT_KEYS
    # Aggregate-only: no node, server, IP, hostname, source, credential, raw
    # sample, or HMAC key material may appear in the receipt document.
    serialized = json.dumps(receipt)
    for forbidden in (
        "server",
        "hostname",
        "password",
        "secret",
        "hmac",
        "key",
        "samples",
        "fixture.invalid",
        "198.51.100.",
        "subscription",
        "CLASH_RELAY_CARRIER_HMAC_KEY",
    ):
        assert forbidden not in serialized
    assert receipt["collected_epoch"] == 1000
    assert receipt["validated_sha"] == SHA


def test_bare_carrier_payload_is_not_a_receipt() -> None:
    with pytest.raises(ValidationError):
        parse_carrier_observation_receipt(_aggregate(), env=ENV)


def test_receipt_requires_validated_sha_and_repository_key() -> None:
    with pytest.raises(ValidationError, match="validated SHA"):
        build_carrier_observation_receipt(aggregate=_aggregate(), validated_sha="", env=ENV)
    with pytest.raises(ValidationError, match="HMAC key"):
        build_carrier_observation_receipt(
            aggregate=_aggregate(),
            validated_sha=SHA,
            env={"GITHUB_REPOSITORY": ENV["GITHUB_REPOSITORY"]},
        )
    wrong_key = {
        **ENV,
        "CLASH_RELAY_CARRIER_HMAC_KEY": "other-repository-bound-carrier-receipt-key",
    }
    with pytest.raises(ValidationError, match="authentication mismatch"):
        parse_carrier_observation_receipt(_receipt(), env=wrong_key)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update({"aggregate_digest": "0" * 64}),
        lambda r: r.update({"binding_digest": "0" * 64}),
        lambda r: r.update({"collected_epoch": 1001}),
        lambda r: r.update({"validated_sha": ""}),
        lambda r: r.update({"validated_sha": "z" * 40}),
        lambda r: r.update({"kind": "other"}),
        lambda r: r.update({"receipt_schema_version": 99}),
        lambda r: r.update({"extra": 1}),
        lambda r: r.pop("binding_digest"),
        lambda r: r["aggregate"].update({"sample_set_id": "f" * 32}),
        lambda r: r["aggregate"].update({"collected_at_epoch": 1002}),
        lambda r: r["aggregate"].update({"sampler_version": 2}),
    ],
)
def test_receipt_mutations_fail_closed(mutate: object) -> None:
    receipt = _receipt()
    mutate(receipt)  # type: ignore[operator]
    with pytest.raises(ValidationError):
        parse_carrier_observation_receipt(receipt, env=ENV)


def test_recomputed_public_digests_cannot_forge_preflight_receipt() -> None:
    receipt = _receipt()
    receipt["aggregate"]["carriers"]["telecom"]["reachable"] = 0
    receipt["aggregate_digest"] = hashlib.sha256(
        json.dumps(receipt["aggregate"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(ValidationError, match="authentication mismatch"):
        parse_carrier_observation_receipt(receipt, env=ENV)


def test_commit_requires_an_expected_validated_sha() -> None:
    receipt = _receipt()
    with pytest.raises(ValidationError, match=r"requires (an expected validated SHA|GITHUB_SHA)"):
        commit_carrier_observation_receipt(
            project=_project(), receipt=receipt, env=ENV, expect_validated_sha="  "
        )
    with pytest.raises(ValidationError, match=r"requires (an expected validated SHA|GITHUB_SHA)"):
        commit_carrier_observation_receipt(project=_project(), receipt=receipt, env=ENV)


def test_commit_fails_closed_on_validated_sha_mismatch() -> None:
    receipt = _receipt()
    with pytest.raises(ValidationError, match="validated SHA mismatch"):
        commit_carrier_observation_receipt(
            project=_project(),
            receipt=receipt,
            env=ENV,
            expect_validated_sha="b" * 40,
        )


def _project():
    return SimpleNamespace(config={"publishing": {"cloudflare_kv": {"key": "config"}}})


@pytest.fixture
def memory_kv(monkeypatch):
    storage: dict[str, bytes] = {}
    calls: list[str] = []

    class Publisher:
        def __init__(self, **_kwargs):
            pass

        def read(self):
            calls.append("read")
            return storage.get("config.carrier-observation-history-v1")

        def publish(self, *, content):
            calls.append("publish")
            storage["config.carrier-observation-history-v1"] = content
            return {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}

    monkeypatch.setattr("clash_relay.carrier_history_application.CloudflareKVPublisher", Publisher)
    return storage, calls


def test_successful_commit_writes_history_exactly_once_and_retries_idempotently(
    memory_kv,
) -> None:
    storage, calls = memory_kv
    receipt = _receipt()
    first = commit_carrier_observation_receipt(
        project=_project(),
        receipt=receipt,
        env=ENV,
        expect_validated_sha=SHA,
        now_epoch=1001,
    )
    assert first["status"] == "published"
    assert calls == ["read", "publish"]
    retry = commit_carrier_observation_receipt(
        project=_project(),
        receipt=receipt,
        env=ENV,
        expect_validated_sha=SHA,
        now_epoch=1011,
    )
    assert retry["status"] == "unchanged"
    assert calls == ["read", "publish", "read"]
    persisted = storage["config.carrier-observation-history-v1"]
    assert b"telecom" in persisted
    for forbidden in (b"sample_set_id", b"hostname", b"password", b"198.51.100."):
        assert forbidden not in persisted


def test_commit_rejects_future_collected_epoch(memory_kv) -> None:
    _, calls = memory_kv
    receipt = build_carrier_observation_receipt(
        aggregate=_aggregate(collected=2000), validated_sha=SHA, env=ENV
    )
    with pytest.raises(ValidationError):
        commit_carrier_observation_receipt(
            project=_project(),
            receipt=receipt,
            env=ENV,
            expect_validated_sha=SHA,
            now_epoch=1500,
        )
    assert calls == []
