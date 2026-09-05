"""Privacy-safe operational SLO outcomes for production attempts."""

from __future__ import annotations

import json
import math
import time
from enum import StrEnum
from typing import Any

from .qualification_reliability import QualificationStageRejected

_STATE_VERSION = 1
_MAX_ATTEMPTS = 60
_MAX_DURATION_MS = 24 * 60 * 60 * 1000
_SHA256_LENGTH = 64


class ProductionOutcome(StrEnum):
    PASSED = "passed"
    QUALIFICATION_REJECTED = "qualification_rejected"
    PROMOTION_BLOCKED = "promotion_blocked"
    FAILED = "failed"


def empty_slo_state() -> dict[str, Any]:
    return {"version": _STATE_VERSION, "attempts": []}


def qualification_failure_category(error: BaseException) -> str | None:
    """Recover the typed qualification category without parsing exception text."""

    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, QualificationStageRejected):
            return current.category.value
        current = current.__cause__ or current.__context__
    return None


def qualification_retry_attempted(error: BaseException) -> bool:
    """Return whether a typed transient qualification failure reached a retry path."""

    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, QualificationStageRejected):
            return current.retryable
        current = current.__cause__ or current.__context__
    return False


def _safe_sha(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) != _SHA256_LENGTH:
        return None
    lowered = value.lower()
    return lowered if all(character in "0123456789abcdef" for character in lowered) else None


def _safe_non_negative_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _safe_duration(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    duration = float(value)
    if not math.isfinite(duration) or duration < 0 or duration > _MAX_DURATION_MS:
        return None
    return round(duration, 3)


def _clean_attempt(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    epoch = _safe_non_negative_int(value.get("epoch"))
    duration_ms = _safe_duration(value.get("duration_ms"))
    try:
        outcome = ProductionOutcome(str(value.get("outcome")))
    except ValueError:
        return None
    if epoch is None or duration_ms is None:
        return None

    clean: dict[str, Any] = {
        "epoch": epoch,
        "outcome": outcome.value,
        "duration_ms": duration_ms,
        "retry_attempted": value.get("retry_attempted") is True,
        "retry_recovered": value.get("retry_recovered") is True,
        "promotion_guard_checked": value.get("promotion_guard_checked") is True,
        "promotion_guard_blocked": value.get("promotion_guard_blocked") is True,
    }
    sha = _safe_sha(value.get("candidate_sha256"))
    if sha is not None:
        clean["candidate_sha256"] = sha
    candidate_bytes = _safe_non_negative_int(value.get("candidate_bytes"))
    if candidate_bytes is not None:
        clean["candidate_bytes"] = candidate_bytes
    category = value.get("qualification_failure_category")
    if isinstance(category, str) and 0 < len(category) <= 64:
        clean["qualification_failure_category"] = category
    return clean


def build_slo_attempt(
    *,
    outcome: ProductionOutcome,
    duration_ms: float,
    candidate_sha256: str | None = None,
    candidate_bytes: int | None = None,
    qualification_failure_category: str | None = None,
    retry_attempted: bool = False,
    retry_recovered: bool = False,
    promotion_guard_checked: bool = False,
    promotion_guard_blocked: bool = False,
    epoch: int | None = None,
) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "epoch": int(time.time()) if epoch is None else epoch,
        "outcome": outcome.value,
        "duration_ms": duration_ms,
        "retry_attempted": retry_attempted,
        "retry_recovered": retry_recovered,
        "promotion_guard_checked": promotion_guard_checked,
        "promotion_guard_blocked": promotion_guard_blocked,
    }
    if candidate_sha256 is not None:
        raw["candidate_sha256"] = candidate_sha256
    if candidate_bytes is not None:
        raw["candidate_bytes"] = candidate_bytes
    if qualification_failure_category is not None:
        raw["qualification_failure_category"] = qualification_failure_category
    clean = _clean_attempt(raw)
    if clean is None:
        raise ValueError("invalid operational SLO attempt")
    if clean["retry_recovered"] and not clean["retry_attempted"]:
        raise ValueError("retry recovery requires a retry attempt")
    if clean["promotion_guard_blocked"] and not clean["promotion_guard_checked"]:
        raise ValueError("promotion guard block requires a guard check")
    return clean


def parse_slo_bytes(content: bytes | None) -> tuple[dict[str, Any], str]:
    if not content:
        return empty_slo_state(), "missing"
    try:
        document = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return empty_slo_state(), "invalid"
    if not isinstance(document, dict) or document.get("version") != _STATE_VERSION:
        return empty_slo_state(), "invalid"
    attempts = document.get("attempts")
    if not isinstance(attempts, list):
        return empty_slo_state(), "invalid"
    clean: list[dict[str, Any]] = []
    for raw in attempts:
        item = _clean_attempt(raw)
        if item is None:
            return empty_slo_state(), "invalid"
        clean.append(item)
    return {"version": _STATE_VERSION, "attempts": clean[-_MAX_ATTEMPTS:]}, "loaded"


def append_slo_attempt(state: dict[str, Any], attempt: dict[str, Any]) -> dict[str, Any]:
    clean_attempt = _clean_attempt(attempt)
    if clean_attempt is None:
        raise ValueError("invalid operational SLO attempt")
    attempts: list[dict[str, Any]] = []
    existing = state.get("attempts")
    if isinstance(existing, list):
        for raw in existing:
            item = _clean_attempt(raw)
            if item is not None:
                attempts.append(item)
    attempts.append(clean_attempt)
    return {"version": _STATE_VERSION, "attempts": attempts[-_MAX_ATTEMPTS:]}


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def slo_summary(state: dict[str, Any]) -> dict[str, Any]:
    attempts = state.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        return {"attempts": 0}

    clean = [item for raw in attempts if (item := _clean_attempt(raw)) is not None]
    outcomes = {outcome.value: 0 for outcome in ProductionOutcome}
    qualification_categories: dict[str, int] = {}
    retry_attempts = 0
    retry_recoveries = 0
    guard_checks = 0
    guard_blocks = 0
    durations: list[float] = []
    candidate_sequence: list[tuple[str, int | None]] = []

    for item in clean:
        outcomes[item["outcome"]] += 1
        category = item.get("qualification_failure_category")
        if isinstance(category, str):
            qualification_categories[category] = qualification_categories.get(category, 0) + 1
        retry_attempts += int(item["retry_attempted"])
        retry_recoveries += int(item["retry_recovered"])
        guard_checks += int(item["promotion_guard_checked"])
        guard_blocks += int(item["promotion_guard_blocked"])
        durations.append(float(item["duration_ms"]))
        sha = item.get("candidate_sha256")
        if isinstance(sha, str):
            candidate_sequence.append((sha, item.get("candidate_bytes")))

    candidate_transitions = max(0, len(candidate_sequence) - 1)
    candidate_changes = sum(
        previous[0] != current[0]
        for previous, current in zip(candidate_sequence, candidate_sequence[1:], strict=False)
    )
    latest_bytes_delta: int | None = None
    if len(candidate_sequence) >= 2:
        previous_bytes = candidate_sequence[-2][1]
        current_bytes = candidate_sequence[-1][1]
        if isinstance(previous_bytes, int) and isinstance(current_bytes, int):
            latest_bytes_delta = current_bytes - previous_bytes

    qualification_rejections = outcomes[ProductionOutcome.QUALIFICATION_REJECTED.value]
    summary: dict[str, Any] = {
        "attempts": len(clean),
        "outcomes": outcomes,
        "qualification_rejections": qualification_rejections,
        "qualification_rejection_rate": _rate(qualification_rejections, len(clean)),
        "qualification_rejections_by_category": dict(sorted(qualification_categories.items())),
        "retry_attempts": retry_attempts,
        "retry_recoveries": retry_recoveries,
        "retry_recovery_rate": _rate(retry_recoveries, retry_attempts),
        "promotion_guard_checks": guard_checks,
        "promotion_guard_blocks": guard_blocks,
        "promotion_guard_block_rate": _rate(guard_blocks, guard_checks),
        "lifecycle_duration_ms": {
            "p50": _percentile(durations, 0.50),
            "p95": _percentile(durations, 0.95),
            "max": round(max(durations), 3),
        },
        "candidate_transitions": candidate_transitions,
        "candidate_changes": candidate_changes,
        "candidate_churn_rate": _rate(candidate_changes, candidate_transitions),
    }
    if latest_bytes_delta is not None:
        summary["latest_candidate_bytes_delta"] = latest_bytes_delta
    return summary
