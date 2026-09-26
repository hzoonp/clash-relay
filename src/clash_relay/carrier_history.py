"""Bounded, aggregate-only carrier observation history.

This state is deliberately independent of scheduler and AI cache state. It
contains no probe plan IDs, endpoint identities, source names, or samples.

Commit boundary (fail closed): only campaigns that already passed candidate
binding may enter this state. The application layer accepts observation input
exclusively from a validated carrier-observation receipt issued by a fully
successful production preflight, so unbound evidence can never be persisted
and ``binding_failed`` is not a representable status.

Statuses are outcomes of a bound campaign:
``valid`` / ``stale`` / ``partial`` / ``insufficient`` describe qualifying
dimensions; ``invalid`` is reserved for a bound campaign whose aggregate
semantics are internally inconsistent.

Counter semantics (explicit, not rolling):
- ``recent_campaign_count`` counts retained campaigns inside the trailing
  ``window_days`` (30-day) window, capped at ``MAX_CAMPAIGNS``. Each entry has
  only a privacy-safe receipt digest and epoch. Duplicate digests are ignored.
  Older epochs do not update counters or EMAs. Equal-epoch, distinct digests
  each count once and update EMAs in serialized commit order.
- ``status_counts_lifetime`` and each carrier's ``campaign_runs_lifetime`` are
  saturating lifetime counters capped at ``MAX_CAMPAIGNS``. They are NOT
  rolling 30-day values. A carrier row (including its run counter) resets only
  after ``window_days`` without a bound valid campaign for that carrier or on
  a full state reset; status counts reset only on a full state reset.
- ``latency_sample_runs`` counts the subset of a carrier's lifetime campaigns
  that actually contributed latency samples (``reachable > 0``), and
  ``last_latency_epoch`` is the epoch of the most recent such campaign. When
  no reachable endpoint was sampled, latency EMAs keep their previous values
  and ``last_latency_epoch`` shows they are not current evidence; latency is
  never rewritten to zero.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any, cast

SCHEMA_VERSION = 3
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
    "insufficient",
    "invalid",
    "partial",
    "stale",
    "valid",
)
WINDOW_DAYS = 30
_CAMPAIGN_ID = re.compile(r"[0-9a-f]{64}\Z")


def _empty_carrier() -> dict[str, Any]:
    return {
        "campaign_runs_lifetime": 0,
        "latency_sample_runs": 0,
        "reachable_ratio_ema": None,
        "median_latency_ms_ema": None,
        "p90_latency_ms_ema": None,
        "outcome_ratio_ema": dict.fromkeys(OUTCOMES),
        "last_seen_epoch": None,
        "last_latency_epoch": None,
    }


def empty_history() -> dict[str, Any]:
    """Return a fresh versioned state with only fixed aggregate keys."""
    return {
        "schema_version": SCHEMA_VERSION,
        "window_days": WINDOW_DAYS,
        "last_updated_epoch": None,
        "last_campaign_epoch": None,
        "recent_campaigns": [],
        "recent_campaign_count": 0,
        "consecutive_valid_campaigns": 0,
        "status_counts_lifetime": dict.fromkeys(STATUSES, 0),
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
    if value.get("schema_version") != SCHEMA_VERSION or value.get("window_days") != WINDOW_DAYS:
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
    recent = value.get("recent_campaigns")
    if (
        not isinstance(recent, list)
        or len(recent) > MAX_CAMPAIGNS
        or any(
            not isinstance(item, Mapping)
            or set(item) != {"campaign_id", "epoch"}
            or not isinstance(item.get("campaign_id"), str)
            or _CAMPAIGN_ID.fullmatch(item["campaign_id"]) is None
            or not _count(item.get("epoch"), maximum=2**53)
            for item in recent
        )
        or recent != sorted(recent, key=lambda item: (item["epoch"], item["campaign_id"]))
        or len({item["campaign_id"] for item in recent}) != len(recent)
        or value["recent_campaign_count"] != len(recent)
        or (updated is not None and any(item["epoch"] > updated for item in recent))
        or (campaign is not None and any(item["epoch"] > campaign for item in recent))
    ):
        return False
    statuses = value.get("status_counts_lifetime")
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
        runs = row.get("campaign_runs_lifetime")
        latency_runs = row.get("latency_sample_runs")
        if (
            not _count(runs)
            or not _count(latency_runs)
            or cast(int, latency_runs) > cast(int, runs)
        ):
            return False
        last = row.get("last_seen_epoch")
        last_latency = row.get("last_latency_epoch")
        for epoch in (last, last_latency):
            if epoch is not None and (
                not _count(epoch, maximum=2**53)
                or (now_epoch is not None and epoch > now_epoch)
                or (updated is not None and epoch > updated)
            ):
                return False
        if (
            last is not None
            and last_latency is not None
            and cast(int, last_latency) > cast(int, last)
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
        if runs == 0:
            if (
                last is not None
                or latency_runs != 0
                or last_latency is not None
                or any(
                    row[name] is not None
                    for name in (
                        "reachable_ratio_ema",
                        "median_latency_ms_ema",
                        "p90_latency_ms_ema",
                    )
                )
                or any(v is not None for v in outcomes.values())
            ):
                return False
        else:
            if (
                last is None
                or row["reachable_ratio_ema"] is None
                or any(value is None for value in outcomes.values())
            ):
                return False
            # Latency evidence must agree with its own sample counter: a
            # carrier with latency samples has a last_latency_epoch and EMA
            # values; without samples the latency EMAs must stay unset.
            if (cast(int, latency_runs) > 0) != (
                last_latency is not None and row["median_latency_ms_ema"] is not None
            ):
                return False
            if latency_runs == 0 and row["p90_latency_ms_ema"] is not None:
                return False
    return True


def _reap_window(history: dict[str, Any], now_epoch: int) -> dict[str, Any]:
    """Drop campaigns and carrier rows that left the bounded window."""
    history["recent_campaigns"] = [
        item for item in history["recent_campaigns"] if now_epoch - item["epoch"] <= MAX_AGE_SECONDS
    ]
    history["recent_campaign_count"] = len(history["recent_campaigns"])
    for carrier in CARRIERS:
        row = history["carriers"][carrier]
        last_seen = row["last_seen_epoch"]
        if last_seen is not None and now_epoch - last_seen > MAX_AGE_SECONDS:
            history["carriers"][carrier] = _empty_carrier()
    return history


def parse_history_bytes(data: bytes | None, *, now_epoch: int) -> dict[str, Any]:
    """Safely reset expired, malformed, oversized, or identity-bearing state.

    A successfully parsed state is reaped against ``now_epoch`` so the trailing
    window counters never report campaigns that already left the window.
    """
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
    if not _valid_history(value, now_epoch):
        return empty_history()
    return _reap_window(cast(dict[str, Any], value), now_epoch)


def _campaign_status(report: Mapping[str, Any]) -> str:
    coverage = report.get("coverage", report.get("status"))
    freshness = report.get("freshness")
    evidence = report.get("evidence")
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
    report: Mapping[str, Any],
    *,
    now_epoch: int,
    campaign_id: str,
    binding_passed: bool = True,
) -> dict[str, Any]:
    """Record one already-bound campaign; update quality only when valid.

    ``report`` must come from a validated receipt commit path; unbound or
    unconfigured evidence has no representable status here. ``campaign_id``
    is the 64-character lowercase digest derived from that validated receipt.
    """
    if binding_passed is not True:
        raise ValueError("unbound carrier campaign must not enter observation history")
    if not isinstance(report, Mapping) or not report:
        raise ValueError("carrier observation requires a bound aggregate report")
    if not isinstance(campaign_id, str) or _CAMPAIGN_ID.fullmatch(campaign_id) is None:
        raise ValueError("carrier campaign_id must be a 64-character lowercase digest")
    state = (
        json.loads(serialize_history(history))
        if _valid_history(history, now_epoch)
        else empty_history()
    )
    state = _reap_window(state, now_epoch)
    freshness = report.get("freshness") if isinstance(report, Mapping) else None
    collected = freshness.get("collected_at_epoch") if isinstance(freshness, Mapping) else None
    campaign_epoch = (
        collected
        if isinstance(collected, int)
        and not isinstance(collected, bool)
        and 0 <= collected <= now_epoch
        else now_epoch
    )
    if any(item["campaign_id"] == campaign_id for item in state["recent_campaigns"]):
        return state
    # A late receipt cannot move quality EMAs backwards in observation time.
    # Equal-second, distinct receipts are independent campaigns: the commit
    # lock determines their order, and both contribute once.
    if state["last_campaign_epoch"] is not None and campaign_epoch < state["last_campaign_epoch"]:
        return state
    if (
        len(state["recent_campaigns"]) >= MAX_CAMPAIGNS
        and state["recent_campaigns"][0]["epoch"] == campaign_epoch
    ):
        raise ValueError("same-epoch carrier campaign history capacity exceeded")
    status = _campaign_status(report)
    state["last_updated_epoch"] = now_epoch
    state["last_campaign_epoch"] = campaign_epoch
    recent = [
        item for item in state["recent_campaigns"] if now_epoch - item["epoch"] <= MAX_AGE_SECONDS
    ]
    if now_epoch - campaign_epoch <= MAX_AGE_SECONDS:
        recent.append({"campaign_id": campaign_id, "epoch": campaign_epoch})
    state["recent_campaigns"] = sorted(
        recent, key=lambda item: (item["epoch"], item["campaign_id"])
    )[-MAX_CAMPAIGNS:]
    state["recent_campaign_count"] = len(state["recent_campaigns"])
    state["status_counts_lifetime"][status] = min(
        MAX_CAMPAIGNS, state["status_counts_lifetime"][status] + 1
    )
    state["consecutive_valid_campaigns"] = (
        min(MAX_CAMPAIGNS, state["consecutive_valid_campaigns"] + 1) if status == "valid" else 0
    )
    if status != "valid":
        return state
    for carrier in CARRIERS:
        measured = report["carriers"][carrier]
        row = state["carriers"][carrier]
        tested = measured["tested"]
        reachable = measured["reachable"]
        row["campaign_runs_lifetime"] = min(MAX_CAMPAIGNS, row["campaign_runs_lifetime"] + 1)
        row["reachable_ratio_ema"] = _ema(row["reachable_ratio_ema"], reachable / tested)
        if reachable > 0:
            # Only campaigns that actually sampled a reachable endpoint move
            # latency evidence; a zero-reachable campaign never fakes latency 0.
            row["latency_sample_runs"] = min(MAX_CAMPAIGNS, row["latency_sample_runs"] + 1)
            for field in ("median_latency_ms", "p90_latency_ms"):
                value = measured.get(field)
                if value is not None:
                    target = f"{field}_ema"
                    row[target] = _ema(row[target], value)
            row["last_latency_epoch"] = campaign_epoch
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
