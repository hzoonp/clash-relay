"""Public declarative scheduler settings with conservative defaults."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .util import load_yaml_file


@dataclass(frozen=True)
class BrowsingSchedulerPolicy:
    attempts: int = 3
    reserve_successes: int = 2


@dataclass(frozen=True)
class HistorySchedulerPolicy:
    min_runs: int = 2
    min_success_ema: float = 0.80
    max_age_seconds: int = 30 * 24 * 60 * 60


@dataclass(frozen=True)
class AICachePolicy:
    pass_ttl_seconds: int = 6 * 60 * 60
    failure_ttl_seconds: int = 60 * 60


@dataclass(frozen=True)
class SchedulerPolicy:
    browsing: BrowsingSchedulerPolicy = BrowsingSchedulerPolicy()
    history: HistorySchedulerPolicy = HistorySchedulerPolicy()
    ai_cache: AICachePolicy = AICachePolicy()


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be a mapping")
    return value


def load_scheduler_policy(path: Path) -> SchedulerPolicy:
    document = load_yaml_file(path)
    if not isinstance(document, dict):
        raise ValidationError("policies document must be a mapping")
    scheduler = _mapping(document.get("scheduler"), "scheduler policy")
    browsing = _mapping(scheduler.get("browsing"), "scheduler.browsing")
    history = _mapping(scheduler.get("history"), "scheduler.history")
    ai_cache = _mapping(scheduler.get("ai_cache"), "scheduler.ai_cache")

    result = SchedulerPolicy(
        browsing=BrowsingSchedulerPolicy(
            attempts=int(browsing.get("attempts", 3)),
            reserve_successes=int(browsing.get("reserve_successes", 2)),
        ),
        history=HistorySchedulerPolicy(
            min_runs=int(history.get("min_runs", 2)),
            min_success_ema=float(history.get("min_success_ema", 0.80)),
            max_age_seconds=int(history.get("max_age_seconds", 30 * 24 * 60 * 60)),
        ),
        ai_cache=AICachePolicy(
            pass_ttl_seconds=int(ai_cache.get("pass_ttl_seconds", 6 * 60 * 60)),
            failure_ttl_seconds=int(ai_cache.get("failure_ttl_seconds", 60 * 60)),
        ),
    )
    if result.browsing.attempts < 1 or result.browsing.attempts > 10:
        raise ValidationError("scheduler.browsing.attempts must be between 1 and 10")
    if (
        result.browsing.reserve_successes < 1
        or result.browsing.reserve_successes > result.browsing.attempts
    ):
        raise ValidationError("scheduler.browsing.reserve_successes must be within attempts")
    if result.history.min_runs < 1 or result.history.min_runs > 100:
        raise ValidationError("scheduler.history.min_runs must be between 1 and 100")
    if not 0.0 <= result.history.min_success_ema <= 1.0:
        raise ValidationError("scheduler.history.min_success_ema must be between 0 and 1")
    if result.history.max_age_seconds < 3600 or result.history.max_age_seconds > 90 * 24 * 60 * 60:
        raise ValidationError("scheduler.history.max_age_seconds must be between 1 hour and 90 days")
    if result.ai_cache.pass_ttl_seconds < 60 or result.ai_cache.pass_ttl_seconds > 24 * 60 * 60:
        raise ValidationError("scheduler.ai_cache.pass_ttl_seconds must be between 60s and 24h")
    if (
        result.ai_cache.failure_ttl_seconds < 60
        or result.ai_cache.failure_ttl_seconds > result.ai_cache.pass_ttl_seconds
    ):
        raise ValidationError(
            "scheduler.ai_cache.failure_ttl_seconds must be between 60s and pass TTL"
        )
    return result
