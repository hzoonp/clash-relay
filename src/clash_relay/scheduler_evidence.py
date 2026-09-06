"""Aggregate-only evidence compiler for future scheduler policy experiments.

This module intentionally does not rank nodes or mutate runtime topology.  It
consumes the privacy-safe production metrics state and emits only bounded,
aggregate signals that can be reviewed before any scheduler behavior changes.
"""

from __future__ import annotations

from typing import Any

_MIN_EVIDENCE_RUNS = 3
_RECENT_EVENT_WINDOW = 10


def _non_negative_int(value: Any) -> int:
    return (
        int(value)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else 0
    )


def _runs(state: dict[str, Any]) -> list[dict[str, Any]]:
    value = state.get("runs")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _failures(state: dict[str, Any]) -> list[dict[str, Any]]:
    value = state.get("failures")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _stable_regions(browsing: dict[str, Any]) -> list[str]:
    regions = browsing.get("regions")
    if not isinstance(regions, dict):
        return []
    return sorted(
        str(region)
        for region, summary in regions.items()
        if isinstance(region, str)
        and region
        and isinstance(summary, dict)
        and _non_negative_int(summary.get("stable")) > 0
    )


def _service_coverage(ai: dict[str, Any]) -> dict[str, int]:
    value = ai.get("qualified_by_service")
    if not isinstance(value, dict):
        return {}
    return {
        str(name): _non_negative_int(count)
        for name, count in sorted(value.items())
        if isinstance(name, str) and name
    }


def _failure_trend(state: dict[str, Any]) -> tuple[float, int]:
    events: list[tuple[int, bool]] = []
    for run in _runs(state):
        epoch = run.get("epoch")
        if (
            isinstance(epoch, int)
            and not isinstance(epoch, bool)
            and epoch >= 0
        ):
            events.append((epoch, False))
    for failure in _failures(state):
        epoch = failure.get("epoch")
        if (
            isinstance(epoch, int)
            and not isinstance(epoch, bool)
            and epoch >= 0
        ):
            events.append((epoch, True))
    events.sort(key=lambda item: (item[0], item[1]))
    recent = events[-_RECENT_EVENT_WINDOW:]
    failure_rate = (
        round(sum(1 for _, failed in recent if failed) / len(recent), 3) if recent else 0.0
    )
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


def compile_scheduler_evidence(state: dict[str, Any]) -> dict[str, Any]:
    """Compile reviewable scheduler evidence without exposing node identity.

    The returned document is deliberately observation-only.  No field is a
    selector score, weight, or routing decision.
    """

    runs = _runs(state)
    latest = runs[-1] if runs else {}
    browsing = (
        latest.get("browsing")
        if isinstance(latest.get("browsing"), dict)
        else {}
    )
    ai = latest.get("ai") if isinstance(latest.get("ai"), dict) else {}
    services = _service_coverage(ai)
    regions = _stable_regions(browsing)
    failure_rate, failure_streak = _failure_trend(state)
    retry_runs, retry_recoveries = _retry_summary(runs)
    promotion = (
        latest.get("promotion_guard")
        if isinstance(latest.get("promotion_guard"), dict)
        else {}
    )

    return {
        "status": (
            "ready" if len(runs) >= _MIN_EVIDENCE_RUNS else "insufficient_history"
        ),
        "mode": "observe_only",
        "privacy": "aggregate_only",
        "sample_runs": len(runs),
        "minimum_sample_runs": _MIN_EVIDENCE_RUNS,
        "browsing": {
            "qualified_nodes": _non_negative_int(browsing.get("qualified")),
            "stable_nodes": _non_negative_int(browsing.get("stable")),
            "historically_demoted_nodes": _non_negative_int(
                browsing.get("historically_demoted")
            ),
            "stable_region_count": len(regions),
            "stable_regions": regions,
        },
        "services": {
            "qualified_by_service": services,
            "covered_service_count": sum(
                1 for count in services.values() if count > 0
            ),
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
