"""Aggregate-only carrier observation persistence and quality gates."""

import hashlib
import json

import pytest

from clash_relay.carrier_history import (
    MAX_AGE_SECONDS,
    MAX_CAMPAIGNS,
    WINDOW_DAYS,
    empty_history,
    parse_history_bytes,
    safe_history_summary,
    serialize_history,
)
from clash_relay.carrier_history import (
    observe_campaign as _observe_campaign,
)


def observe_campaign(history, campaign, *, now_epoch, campaign_id=None, **kwargs):
    """Supply a stable digest fixture while exercising the strict public API."""
    if campaign_id is None:
        epoch = (
            campaign.get("freshness", {}).get("collected_at_epoch", now_epoch)
            if campaign
            else now_epoch
        )
        payload = json.dumps([campaign, epoch], sort_keys=True).encode()
        campaign_id = hashlib.sha256(payload).hexdigest()
    return _observe_campaign(
        history, campaign, now_epoch=now_epoch, campaign_id=campaign_id, **kwargs
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
    first = observe_campaign(empty_history(), report(), now_epoch=100)
    assert first["recent_campaign_count"] == 1
    assert first["consecutive_valid_campaigns"] == 1
    for row in first["carriers"].values():
        assert row["campaign_runs_lifetime"] == 1
        assert row["latency_sample_runs"] == 1
        assert row["last_latency_epoch"] == 100
        assert row["reachable_ratio_ema"] == 0.8
        assert row["median_latency_ms_ema"] == 42
        assert row["p90_latency_ms_ema"] == 55
        assert row["outcome_ratio_ema"]["dns_failure"] == 0.2
        assert row["last_seen_epoch"] == 100
    changed = report()
    for row in changed["carriers"].values():
        row.update(reachable=5, median_latency_ms=20.0, p90_latency_ms=30.0)
        row["outcomes"].update(dns_failure=0, tcp_connected=5)
    second = observe_campaign(first, changed, now_epoch=110)
    assert second["carriers"]["telecom"]["reachable_ratio_ema"] == 0.85
    assert second["carriers"]["telecom"]["median_latency_ms_ema"] == 36.5
    assert second["carriers"]["telecom"]["latency_sample_runs"] == 2
    assert second["consecutive_valid_campaigns"] == 2


def test_zero_reachable_campaign_keeps_latency_evidence_semantics():
    state = observe_campaign(empty_history(), report(), now_epoch=100)
    unreachable = report()
    for row in unreachable["carriers"].values():
        row.update(reachable=0, median_latency_ms=None, p90_latency_ms=None)
        row["outcomes"].update(dns_failure=5, tcp_connected=0)
    updated = observe_campaign(state, unreachable, now_epoch=110)
    for row in updated["carriers"].values():
        # Reachability and outcome evidence of THIS campaign is recorded, but
        # latency EMAs are not rewritten to zero and stay flagged as older
        # evidence through latency_sample_runs / last_latency_epoch.
        assert row["reachable_ratio_ema"] == 0.6
        assert row["outcome_ratio_ema"]["dns_failure"] == 0.4
        assert row["median_latency_ms_ema"] == 42
        assert row["p90_latency_ms_ema"] == 55
        assert row["latency_sample_runs"] == 1
        assert row["last_latency_epoch"] == 100
        assert row["last_seen_epoch"] == 110


@pytest.mark.parametrize(
    ("kwargs", "status"),
    [
        ({"freshness": "stale", "evidence": "stale"}, "stale"),
        ({"coverage": "partial"}, "partial"),
        ({"evidence": "insufficient"}, "insufficient"),
    ],
)
def test_nonqualifying_campaign_only_updates_status(kwargs, status):
    initial = observe_campaign(empty_history(), report(), now_epoch=100)
    updated = observe_campaign(initial, report(**kwargs), now_epoch=110)
    assert updated["carriers"] == initial["carriers"]
    assert updated["status_counts_lifetime"][status] == 1
    assert updated["consecutive_valid_campaigns"] == 0


def test_binding_failed_is_not_a_representable_status():
    assert "binding_failed" not in empty_history()["status_counts_lifetime"]
    state = observe_campaign(empty_history(), report(), now_epoch=100)
    before = serialize_history(state)
    with pytest.raises(ValueError, match="unbound carrier campaign"):
        observe_campaign(state, report(), now_epoch=110, binding_passed=False)
    assert serialize_history(state) == before


def test_unbound_report_is_rejected_before_persistence():
    with pytest.raises(ValueError):
        observe_campaign(empty_history(), None, now_epoch=100)


def test_malformed_report_never_updates_quality():
    malformed = report()
    malformed["carriers"]["telecom"]["outcomes"]["dns_failure"] = 99
    observed = observe_campaign(empty_history(), malformed, now_epoch=100)
    assert observed["status_counts_lifetime"]["invalid"] == 1
    assert observed["carriers"]["telecom"]["campaign_runs_lifetime"] == 0


def test_expired_and_malformed_history_reset_safely():
    state = observe_campaign(empty_history(), report(), now_epoch=100)
    data = serialize_history(state)
    assert parse_history_bytes(data, now_epoch=100) == state
    assert parse_history_bytes(data, now_epoch=100 + MAX_AGE_SECONDS + 1) == empty_history()
    assert parse_history_bytes(b"{not json", now_epoch=100) == empty_history()
    assert (
        parse_history_bytes(b'{"schema_version":3,"schema_version":3}', now_epoch=100)
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
    # A v1 state (or any foreign schema) resets instead of being reinterpreted.
    v1 = serialize_history(state).replace(b'"schema_version":3', b'"schema_version":1')
    assert parse_history_bytes(v1, now_epoch=100) == empty_history()


def test_identity_fields_cannot_enter_persistence_or_summary():
    state = observe_campaign(empty_history(), report(), now_epoch=100)
    state["target_list"] = "198.51.100.10:443 secret-source"
    with pytest.raises(ValueError):
        serialize_history(state)
    summary = safe_history_summary(state)
    assert summary == empty_history()
    raw = json.loads(serialize_history(observe_campaign(empty_history(), report(), now_epoch=100)))
    for word in ("endpoint", "hostname", "server", "source", "sample_set_id", "probe_plan_id"):
        assert word not in json.dumps(raw)


def test_deterministic_and_bounded_persistence():
    state = empty_history()
    for step in range(MAX_CAMPAIGNS + 5):
        state = observe_campaign(state, report(), now_epoch=100 + step)
    assert state["recent_campaign_count"] == MAX_CAMPAIGNS
    assert state["consecutive_valid_campaigns"] == MAX_CAMPAIGNS
    assert state["carriers"]["mobile"]["campaign_runs_lifetime"] == MAX_CAMPAIGNS
    assert state["carriers"]["mobile"]["latency_sample_runs"] == MAX_CAMPAIGNS
    assert serialize_history(state) == serialize_history(
        parse_history_bytes(serialize_history(state), now_epoch=200)
    )


def test_same_collected_campaign_is_idempotent_across_preflight_retries():
    campaign = report()
    campaign["freshness"]["collected_at_epoch"] = 90
    first = observe_campaign(empty_history(), campaign, now_epoch=100)
    repeated = observe_campaign(first, campaign, now_epoch=110)
    assert repeated == first
    assert repeated["last_campaign_epoch"] == 90


def test_two_distinct_campaigns_in_same_second_are_both_observed():
    same_second = report()
    same_second["freshness"]["collected_at_epoch"] = 90
    first = observe_campaign(empty_history(), same_second, now_epoch=100, campaign_id="a" * 64)
    second = observe_campaign(first, same_second, now_epoch=100, campaign_id="b" * 64)
    assert second["recent_campaign_count"] == 2
    assert second["status_counts_lifetime"]["valid"] == 2
    assert second["carriers"]["telecom"]["campaign_runs_lifetime"] == 2
    assert second["recent_campaigns"] == [
        {"campaign_id": "a" * 64, "epoch": 90},
        {"campaign_id": "b" * 64, "epoch": 90},
    ]
    assert observe_campaign(second, same_second, now_epoch=100, campaign_id="a" * 64) == second


def test_same_epoch_capacity_rejects_new_id_before_eviction():
    campaign = report()
    campaign["freshness"]["collected_at_epoch"] = 90
    state = empty_history()
    for index in range(MAX_CAMPAIGNS):
        state = observe_campaign(state, campaign, now_epoch=100, campaign_id=f"{index:064x}")
    with pytest.raises(ValueError, match=r"same-epoch.*capacity"):
        observe_campaign(state, campaign, now_epoch=100, campaign_id=f"{MAX_CAMPAIGNS:064x}")
    assert state["recent_campaign_count"] == MAX_CAMPAIGNS


def test_out_of_order_campaign_does_not_reverse_quality_or_status():
    latest = report()
    latest["freshness"]["collected_at_epoch"] = 110
    state = observe_campaign(empty_history(), latest, now_epoch=120, campaign_id="a" * 64)
    older = report(coverage="partial")
    older["freshness"]["collected_at_epoch"] = 100
    assert observe_campaign(state, older, now_epoch=120, campaign_id="b" * 64) == state


def test_campaign_id_is_a_strict_private_digest():
    for campaign_id in ("not-a-digest", "A" * 64, "x" * 64, "198.51.100.1:443"):
        with pytest.raises(ValueError, match="campaign_id"):
            observe_campaign(empty_history(), report(), now_epoch=100, campaign_id=campaign_id)


def test_recent_campaign_count_uses_a_rolling_age_window():
    state = empty_history()
    for epoch in (100, 100 + 20 * 24 * 3600, 100 + 40 * 24 * 3600):
        state = observe_campaign(state, report(), now_epoch=epoch)
    assert state["recent_campaign_count"] == 2
    assert [item["epoch"] for item in state["recent_campaigns"]] == [
        100 + 20 * 24 * 3600,
        100 + 40 * 24 * 3600,
    ]
    assert state["carriers"]["telecom"]["campaign_runs_lifetime"] == 3
    assert parse_history_bytes(serialize_history(state), now_epoch=100 + 40 * 24 * 3600) == state


def test_parse_reaps_windowed_counters_at_read_time():
    state = observe_campaign(empty_history(), report(), now_epoch=100)
    state = observe_campaign(state, report(), now_epoch=100 + 20 * 24 * 3600)
    data = serialize_history(state)
    assert state["recent_campaign_count"] == 2
    # Reading the state 31 days after the first campaign drops that campaign
    # from the trailing window even before any new campaign is observed; the
    # lifetime counters are saturating and therefore unchanged.
    reaped = parse_history_bytes(data, now_epoch=100 + 31 * 24 * 3600)
    assert reaped["recent_campaign_count"] == 1
    assert [item["epoch"] for item in reaped["recent_campaigns"]] == [100 + 20 * 24 * 3600]
    assert reaped["status_counts_lifetime"]["valid"] == 2
    assert reaped["carriers"]["telecom"]["campaign_runs_lifetime"] == 2


def test_old_quality_ema_expires_during_continuous_invalid_campaigns():
    state = observe_campaign(empty_history(), report(), now_epoch=100)
    for epoch in (100 + 20 * 24 * 3600, 100 + 40 * 24 * 3600):
        state = observe_campaign(state, report(coverage="partial"), now_epoch=epoch)
    assert state["carriers"]["telecom"]["campaign_runs_lifetime"] == 0
    assert state["carriers"]["telecom"]["reachable_ratio_ema"] is None
    assert state["status_counts_lifetime"]["partial"] == 2


def test_counter_semantics_are_self_describing():
    state = empty_history()
    assert state["window_days"] == WINDOW_DAYS
    assert set(state["status_counts_lifetime"]) == {
        "valid",
        "stale",
        "partial",
        "insufficient",
        "invalid",
    }
    assert set(state["carriers"]["telecom"]) == {
        "campaign_runs_lifetime",
        "latency_sample_runs",
        "reachable_ratio_ema",
        "median_latency_ms_ema",
        "p90_latency_ms_ema",
        "outcome_ratio_ema",
        "last_seen_epoch",
        "last_latency_epoch",
    }
