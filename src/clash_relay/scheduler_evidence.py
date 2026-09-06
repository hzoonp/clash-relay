"""Aggregate-only evidence compiler for future scheduler policy experiments.

This module intentionally does not rank nodes or mutate runtime topology. It
consumes the privacy-safe production metrics state and emits only bounded,
aggregate signals that can be reviewed before any scheduler behavior changes.
"""

from __future__ import annotations

from typing import Any

_MIN_EVIDENCE_RUNS = 3
_RECENT_EVENT_WINDOW = 10


def _non_negative_int(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return 0
    return value


def _rows(state: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = state.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _event_epoch(row: dict[str, Any]) -> int | None:
    value = row.get("epoch")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return value


def _stable_regions(browsing: dict[str, Any]) -> list[str]:
    regions = browsing.get("regions")
    if not isinstance(regions, dict):
        return []
    stable: list[str] = []
    for region, summary in regions.items():
        if not isinstance(region, str) or not region:
            continue
        if not isinstance(summary, dict):
            continue
        if _non_negative_int(summary.get("stable")) > 0:
            stable.append(region)
    return sorted(stable)


def _service_coverage(ai: dict[str, Any]) -> dict[str, int]:
    value = ai.get("qualified_by_service")
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for name, count in sorted(value.items()):
        if isinstance(name, str) and name:
            result[name] = _non_negative_int(count)
    return result


def _failure_trend(state: dict[str, Any]) -> tuple[float, int]:
    events: list[tuple[int, bool]] = []
    for run in _rows(state, "runs"):
        epoch = _event_epoch(run)
        if epoch is not None:
            events.append((epoch, False))
    for failure in _rows(state, "failures"):
        epoch = _event_epoch(failure)
        if epoch is not None:
            events.append((epoch, True))
    events.sort(key=lambda item: (item[0], item[1]))
    recent = events[-_RECENT_EVENT_WINDOW:]
    if recent:
        failures = sum(1 for _, failed in recent if failed)
        failure_rate = round(failures / len(recent), 3)
    else:
        failure_rate = 0.0
    streak = 0
    for _, failed in reversed(events):
        if not failed:
            break
        streak += 1
    return failure_rate, streak


def _retry_summary(runs: list[dict[str, Any]]) -> tuple[int, int]:
    retry_runs = 0
    recoveries = 0
    for run in runs:
        qualification = run.get("qualification")
        if not isinstance(qualification, dict):
            continue
        attempts = _non_negative_int(qualification.get("browsing_attempts")) or 1
        if attempts > 1:
            retry_runs += 1
        if qualification.get("recovered_by_retry") is True:
            recoveries += 1
    return retry_runs, recoveries


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def compile_scheduler_evidence(state: dict[str, Any]) -> dict[str, Any]:
    """Compile reviewable scheduler evidence without exposing node identity."""

    runs = _rows(state, "runs")
    latest = runs[-1] if runs else {}
    browsing = _mapping(latest.get("browsing"))
    ai = _mapping(latest.get("ai"))
    promotion = _mapping(latest.get("promotion_guard"))
    services = _service_coverage(ai)
    regions = _stable_regions(browsing)
    failure_rate, failure_streak = _failure_trend(state)
    retry_runs, retry_recoveries = _retry_summary(runs)
    status = "ready" if len(runs) >= _MIN_EVIDENCE_RUNS else "insufficient_history"
    covered_services = sum(1 for count in services.values() if count > 0)

    return {
        "status": status,
        "mode": "observe_only",
        "privacy": "aggregate_only",
        "sample_runs": len(runs),
        "minimum_sample_runs": _MIN_EVIDENCE_RUNS,
        "browsing": {
            "qualified_nodes": _non_negative_int(browsing.get("qualified")),
            "stable_nodes": _non_negative_int(browsing.get("stable")),
            "historically_demoted_nodes": _non_negative_int(browsing.get("historically_demoted")),
            "stable_region_count": len(regions),
            "stable_regions": regions,
        },
        "services": {
            "qualified_by_service": services,
            "covered_service_count": covered_services,
            "service_count": len(services),
            "minimum_qualified_nodes": min(services.values()) if services else 0,
        },
        "reliability": {
            "recent_failure_rate": failure_rate,
            "recent_failure_streak": failure_streak,
            "retry_runs": retry_runs,
            "retry_recoveries": retry_recoveries,
            "latest_promotion_guard_status": promotion.get("status", "unknown"),
        },
    }
