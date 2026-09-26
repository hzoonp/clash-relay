"""Bounded, aggregate-only carrier observation history.

This state is deliberately independent of scheduler and AI cache state. It
contains no probe plan IDs, endpoint identities, source names, or samples.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any, cast

SCHEMA_VERSION = 1
MAX_AGE_SECONDS = 30 * 24 * 3600
MAX_CAMPAIGNS = 64
MAX_STATE_BYTES = 16_384
EMA_ALPHA = 0.25
CARRIERS = ("mobile", "telecom", "unicom")
OUTCOMES = (
    "connect_failure",
    "connect_timeout",
    "connection_refused",
    "dns_failure",
    "tcp_connected",
)
STATUSES = (
    "binding_failed",
    "insufficient",
    "invalid",
    "not_configured",
    "partial",
    "stale",
    "valid",
)


def _empty_carrier() -> dict[str, Any]:
    return {
        "campaign_runs": 0,
        "reachable_ratio_ema": None,
        "median_latency_ms_ema": None,
        "p90_latency_ms_ema": None,
        "outcome_ratio_ema": dict.fromkeys(OUTCOMES),
        "last_seen_epoch": None,
    }


def empty_history() -> dict[str, Any]:
    """Return a fresh versioned state with only fixed aggregate keys."""
    return {
        "schema_version": SCHEMA_VERSION,
        "last_updated_epoch": None,
        "last_campaign_epoch": None,
        "recent_campaign_epochs": [],
        "recent_campaign_count": 0,
        "consecutive_valid_campaigns": 0,
        "status_counts": dict.fromkeys(STATUSES, 0),
        "carriers": {carrier: _empty_carrier() for carrier in CARRIERS},
    }


def _count(value: object, *, maximum: int = MAX_CAMPAIGNS) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum


def _metric(value: object, *, maximum: float = 1.0) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value <= maximum
    )


def _valid_history(value: object, now_epoch: int | None = None) -> bool:
    template = empty_history()
    if not isinstance(value, Mapping) or set(value) != set(template):
        return False
    if value.get("schema_version") != SCHEMA_VERSION:
        return False
    updated = value.get("last_updated_epoch")
    if updated is not None and (
        not _count(updated, maximum=2**53)
        or (
            now_epoch is not None and (updated > now_epoch or now_epoch - updated > MAX_AGE_SECONDS)
        )
    ):
        return False
    campaign = value.get("last_campaign_epoch")
    if campaign is not None and (
        not _count(campaign, maximum=2**53) or updated is None or campaign > updated
    ):
        return False
    if not _count(value.get("recent_campaign_count")) or not _count(
        value.get("consecutive_valid_campaigns")
    ):
        return False
    recent = value.get("recent_campaign_epochs")
    if (
        not isinstance(recent, list)
        or len(recent) > MAX_CAMPAIGNS
        or any(not _count(epoch, maximum=2**53) for epoch in recent)
        or recent != sorted(set(recent))
        or value["recent_campaign_count"] != len(recent)
        or (updated is not None and any(epoch > updated for epoch in recent))
    ):
        return False
    statuses = value.get("status_counts")
    if (
        not isinstance(statuses, Mapping)
        or set(statuses) != set(STATUSES)
        or not all(_count(v) for v in statuses.values())
    ):
        return False
    if updated is None and (
        campaign is not None or value["recent_campaign_count"] != 0 or any(statuses.values())
    ):
        return False
    carriers = value.get("carriers")
    if not isinstance(carriers, Mapping) or set(carriers) != set(CARRIERS):
        return False
    for row in carriers.values():
        if not isinstance(row, Mapping) or set(row) != set(_empty_carrier()):
            return False
        runs = row.get("campaign_runs")
        if not _count(runs):
            return False
        last = row.get("last_seen_epoch")
        if last is not None and (
            not _count(last, maximum=2**53)
            or (now_epoch is not None and last > now_epoch)
            or (updated is not None and last > updated)
        ):
            return False
        for name, maximum in (
            ("reachable_ratio_ema", 1.0),
            ("median_latency_ms_ema", 60_000.0),
            ("p90_latency_ms_ema", 60_000.0),
        ):
            metric = row.get(name)
            if metric is not None and not _metric(metric, maximum=maximum):
                return False
        outcomes = row.get("outcome_ratio_ema")
        if not isinstance(outcomes, Mapping) or set(outcomes) != set(OUTCOMES):
            return False
        if any(v is not None and not _metric(v) for v in outcomes.values()):
            return False
        if runs == 0 and (
            last is not None
            or any(
                row[name] is not None
                for name in ("reachable_ratio_ema", "median_latency_ms_ema", "p90_latency_ms_ema")
            )
            or any(v is not None for v in outcomes.values())
        ):
            return False
        if cast(int, runs) > 0 and (
            last is None
            or row["reachable_ratio_ema"] is None
            or any(value is None for value in outcomes.values())
        ):
            return False
    return True


def parse_history_bytes(data: bytes | None, *, now_epoch: int) -> dict[str, Any]:
    """Safely reset expired, malformed, oversized, or identity-bearing state."""
    if not data or len(data) > MAX_STATE_BYTES:
        return empty_history()

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate history key")
            result[key] = value
        return result

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, ValueError, TypeError):
        return empty_history()
    return value if _valid_history(value, now_epoch) else empty_history()


def _campaign_status(report: Mapping[str, Any] | None, binding_passed: bool) -> str:
    if not binding_passed:
        return "binding_failed"
    if report is None:
        return "not_configured"
    coverage = report.get("coverage", report.get("status"))
    freshness = report.get("freshness")
    evidence = report.get("evidence")
    if coverage == "not_configured":
        return "not_configured"
    if (
        coverage not in {"partial", "full"}
        or not isinstance(freshness, Mapping)
        or not isinstance(evidence, Mapping)
    ):
        return "invalid"
    if freshness.get("status") == "stale" or evidence.get("status") == "stale":
        return "stale"
    if freshness.get("status") != "current":
        return "invalid"
    if coverage == "partial":
        return "partial"
    if evidence.get("status") == "insufficient":
        return "insufficient"
    if evidence.get("status") != "sufficient":
        return "invalid"
    rows = report.get("carriers")
    if not isinstance(rows, Mapping) or set(rows) != set(CARRIERS):
        return "invalid"
    for row in rows.values():
        if not isinstance(row, Mapping):
            return "invalid"
        tested, reachable, sampled = (row.get(key) for key in ("tested", "reachable", "sampled"))
        if (
            not _count(tested, maximum=48)
            or not _count(reachable, maximum=48)
            or not _count(sampled, maximum=48)
        ):
            return "invalid"
        tested, reachable, sampled = cast(int, tested), cast(int, reachable), cast(int, sampled)
        if not (0 < tested == sampled and reachable <= tested):
            return "invalid"
        median, p90 = row.get("median_latency_ms"), row.get("p90_latency_ms")
        if reachable and (
            not _metric(median, maximum=60_000)
            or (p90 is not None and (not _metric(p90, maximum=60_000) or p90 < median))
        ):
            return "invalid"
        if not reachable and (median is not None or p90 is not None):
            return "invalid"
        outcomes = row.get("outcomes")
        if (
            not isinstance(outcomes, Mapping)
            or set(outcomes) != set(OUTCOMES)
            or any(not _count(v, maximum=48) for v in outcomes.values())
            or sum(outcomes.values()) != tested
            or outcomes["tcp_connected"] != reachable
        ):
            return "invalid"
        if row.get("sufficient_evidence") is not True:
            return "invalid"
    return "valid"


def _ema(previous: float | None, current: float) -> float:
    return round(
        current if previous is None else EMA_ALPHA * current + (1 - EMA_ALPHA) * previous, 6
    )


def observe_campaign(
    history: Mapping[str, Any],
    report: Mapping[str, Any] | None,
    *,
    binding_passed: bool,
    now_epoch: int,
) -> dict[str, Any]:
    """Record campaign status; update quality only for a bound, valid campaign."""
    state = (
        json.loads(serialize_history(history))
        if _valid_history(history, now_epoch)
        else empty_history()
    )
    for carrier in CARRIERS:
        last_seen = state["carriers"][carrier]["last_seen_epoch"]
        if last_seen is not None and now_epoch - last_seen > MAX_AGE_SECONDS:
            state["carriers"][carrier] = _empty_carrier()
    freshness = report.get("freshness") if isinstance(report, Mapping) else None
    collected = freshness.get("collected_at_epoch") if isinstance(freshness, Mapping) else None
    campaign_epoch = (
        collected
        if isinstance(collected, int)
        and not isinstance(collected, bool)
        and 0 <= collected <= now_epoch
        else now_epoch
    )
    if state["last_campaign_epoch"] is not None and campaign_epoch <= state["last_campaign_epoch"]:
        return state
    status = _campaign_status(report, binding_passed)
    state["last_updated_epoch"] = now_epoch
    state["last_campaign_epoch"] = campaign_epoch
    recent = [
        epoch for epoch in state["recent_campaign_epochs"] if now_epoch - epoch <= MAX_AGE_SECONDS
    ]
    if now_epoch - campaign_epoch <= MAX_AGE_SECONDS:
        recent.append(campaign_epoch)
    state["recent_campaign_epochs"] = recent[-MAX_CAMPAIGNS:]
    state["recent_campaign_count"] = len(state["recent_campaign_epochs"])
    state["status_counts"][status] = min(MAX_CAMPAIGNS, state["status_counts"][status] + 1)
    state["consecutive_valid_campaigns"] = (
        min(MAX_CAMPAIGNS, state["consecutive_valid_campaigns"] + 1) if status == "valid" else 0
    )
    if status != "valid" or report is None:
        return state
    for carrier in CARRIERS:
        measured = report["carriers"][carrier]
        row = state["carriers"][carrier]
        tested = measured["tested"]
        reachable = measured["reachable"]
        row["campaign_runs"] = min(MAX_CAMPAIGNS, row["campaign_runs"] + 1)
        row["reachable_ratio_ema"] = _ema(row["reachable_ratio_ema"], reachable / tested)
        for field in ("median_latency_ms", "p90_latency_ms"):
            value = measured.get(field)
            if value is not None:
                target = f"{field}_ema"
                row[target] = _ema(row[target], value)
        for outcome in OUTCOMES:
            previous = row["outcome_ratio_ema"][outcome]
            row["outcome_ratio_ema"][outcome] = _ema(
                previous, measured["outcomes"][outcome] / tested
            )
        row["last_seen_epoch"] = campaign_epoch
    return state


def serialize_history(history: Mapping[str, Any]) -> bytes:
    """Persist a strict schema with stable ordering and no arbitrary fields."""
    if not _valid_history(history):
        raise ValueError("invalid carrier observation history")
    return (
        json.dumps(history, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")


def safe_history_summary(history: Mapping[str, Any]) -> dict[str, Any]:
    """Project only the fixed numeric history schema into observability."""
    return json.loads(serialize_history(history)) if _valid_history(history) else empty_history()
