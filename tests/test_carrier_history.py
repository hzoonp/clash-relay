"""Aggregate-only carrier observation persistence and quality gates."""

import json

import pytest

from clash_relay.carrier_history import (
    MAX_AGE_SECONDS,
    MAX_CAMPAIGNS,
    empty_history,
    observe_campaign,
    parse_history_bytes,
    safe_history_summary,
    serialize_history,
)


def report(*, coverage="full", freshness="current", evidence="sufficient"):
    row = {
        "tested": 5,
        "sampled": 5,
        "reachable": 4,
        "median_latency_ms": 42.0,
        "p90_latency_ms": 55.0,
        "sufficient_evidence": True,
        "outcomes": {
            "dns_failure": 1,
            "connect_timeout": 0,
            "connection_refused": 0,
            "connect_failure": 0,
            "tcp_connected": 4,
        },
    }
    return {
        "coverage": coverage,
        "freshness": {"status": freshness},
        "evidence": {"status": evidence},
        "carriers": {key: dict(row) for key in ("telecom", "unicom", "mobile")},
    }


def test_valid_campaign_updates_all_carrier_aggregate_emas():
    first = observe_campaign(empty_history(), report(), binding_passed=True, now_epoch=100)
    assert first["recent_campaign_count"] == 1
    assert first["consecutive_valid_campaigns"] == 1
    for row in first["carriers"].values():
        assert row["campaign_runs"] == 1
        assert row["reachable_ratio_ema"] == 0.8
        assert row["median_latency_ms_ema"] == 42
        assert row["p90_latency_ms_ema"] == 55
        assert row["outcome_ratio_ema"]["dns_failure"] == 0.2
        assert row["last_seen_epoch"] == 100
    changed = report()
    for row in changed["carriers"].values():
        row.update(reachable=5, median_latency_ms=20.0, p90_latency_ms=30.0)
        row["outcomes"].update(dns_failure=0, tcp_connected=5)
    second = observe_campaign(first, changed, binding_passed=True, now_epoch=110)
    assert second["carriers"]["telecom"]["reachable_ratio_ema"] == 0.85
    assert second["carriers"]["telecom"]["median_latency_ms_ema"] == 36.5
    assert second["consecutive_valid_campaigns"] == 2


@pytest.mark.parametrize(
    ("kwargs", "binding", "status"),
    [
        ({"freshness": "stale", "evidence": "stale"}, True, "stale"),
        ({"coverage": "partial"}, True, "partial"),
        ({"evidence": "insufficient"}, True, "insufficient"),
        ({}, False, "binding_failed"),
    ],
)
def test_nonqualifying_campaign_only_updates_status(kwargs, binding, status):
    initial = observe_campaign(empty_history(), report(), binding_passed=True, now_epoch=100)
    updated = observe_campaign(initial, report(**kwargs), binding_passed=binding, now_epoch=110)
    assert updated["carriers"] == initial["carriers"]
    assert updated["status_counts"][status] == 1
    assert updated["consecutive_valid_campaigns"] == 0


def test_malformed_report_never_updates_quality():
    malformed = report()
    malformed["carriers"]["telecom"]["outcomes"]["dns_failure"] = 99
    observed = observe_campaign(empty_history(), malformed, binding_passed=True, now_epoch=100)
    assert observed["status_counts"]["invalid"] == 1
    assert observed["carriers"]["telecom"]["campaign_runs"] == 0


def test_expired_and_malformed_history_reset_safely():
    state = observe_campaign(empty_history(), report(), binding_passed=True, now_epoch=100)
    data = serialize_history(state)
    assert parse_history_bytes(data, now_epoch=100) == state
    assert parse_history_bytes(data, now_epoch=100 + MAX_AGE_SECONDS + 1) == empty_history()
    assert parse_history_bytes(b"{not json", now_epoch=100) == empty_history()
    assert (
        parse_history_bytes(b'{"schema_version":1,"schema_version":1}', now_epoch=100)
        == empty_history()
    )
    assert parse_history_bytes(b"x" * 20_000, now_epoch=100) == empty_history()
    assert (
        parse_history_bytes(
            serialize_history(empty_history()).replace(
                b'"recent_campaign_count":0', b'"recent_campaign_count":1'
            ),
            now_epoch=100,
        )
        == empty_history()
    )


def test_identity_fields_cannot_enter_persistence_or_summary():
    state = observe_campaign(empty_history(), report(), binding_passed=True, now_epoch=100)
    state["target_list"] = "198.51.100.10:443 secret-source"
    with pytest.raises(ValueError):
        serialize_history(state)
    summary = safe_history_summary(state)
    assert summary == empty_history()
    raw = json.loads(
        serialize_history(
            observe_campaign(empty_history(), report(), binding_passed=True, now_epoch=100)
        )
    )
    for word in ("endpoint", "hostname", "server", "source", "sample_set_id", "probe_plan_id"):
        assert word not in json.dumps(raw)


def test_deterministic_and_bounded_persistence():
    state = empty_history()
    for step in range(MAX_CAMPAIGNS + 5):
        state = observe_campaign(state, report(), binding_passed=True, now_epoch=100 + step)
    assert state["recent_campaign_count"] == MAX_CAMPAIGNS
    assert state["consecutive_valid_campaigns"] == MAX_CAMPAIGNS
    assert state["carriers"]["mobile"]["campaign_runs"] == MAX_CAMPAIGNS
    assert serialize_history(state) == serialize_history(
        parse_history_bytes(serialize_history(state), now_epoch=200)
    )


def test_same_collected_campaign_is_idempotent_across_preflight_retries():
    campaign = report()
    campaign["freshness"]["collected_at_epoch"] = 90
    first = observe_campaign(empty_history(), campaign, binding_passed=True, now_epoch=100)
    repeated = observe_campaign(first, campaign, binding_passed=True, now_epoch=110)
    assert repeated == first
    assert repeated["last_campaign_epoch"] == 90


def test_recent_campaign_count_uses_a_rolling_age_window():
    state = empty_history()
    for epoch in (100, 100 + 20 * 24 * 3600, 100 + 40 * 24 * 3600):
        state = observe_campaign(state, report(), binding_passed=True, now_epoch=epoch)
    assert state["recent_campaign_count"] == 2
    assert state["recent_campaign_epochs"] == [
        100 + 20 * 24 * 3600,
        100 + 40 * 24 * 3600,
    ]
    assert state["carriers"]["telecom"]["campaign_runs"] == 3
    assert parse_history_bytes(serialize_history(state), now_epoch=100 + 40 * 24 * 3600) == state


def test_old_quality_ema_expires_during_continuous_invalid_campaigns():
    state = observe_campaign(empty_history(), report(), binding_passed=True, now_epoch=100)
    for epoch in (100 + 20 * 24 * 3600, 100 + 40 * 24 * 3600):
        state = observe_campaign(
            state, report(coverage="partial"), binding_passed=True, now_epoch=epoch
        )
    assert state["carriers"]["telecom"]["campaign_runs"] == 0
    assert state["carriers"]["telecom"]["reachable_ratio_ema"] is None
    assert state["status_counts"]["partial"] == 2
