from __future__ import annotations

from pathlib import Path

import pytest

from clash_relay.errors import ValidationError
from clash_relay.scheduler_history import derive_fingerprint_key, fingerprint_runtime_name
from clash_relay.scheduler_policy import (
    HistorySchedulerPolicy,
    load_scheduler_policy,
    preferred_stable_names_from_policy,
)


def _minimal_policy(path: Path, scheduler: str = "") -> None:
    path.write_text(
        "version: 1\n"
        f"{scheduler}"
        "capabilities:\n"
        "  general: {description: General, restricted: false}\n"
        "cost_levels: [standard]\n"
        "country_classification: {default: OTHER, aliases: {}}\n"
        "probes:\n"
        "  connectivity:\n"
        "    {url: https://example.invalid/204, method: HEAD, expected_status: '204', interval: 300, timeout: 5000, lazy: true, tolerance: 50}\n"
        "pools: []\n"
        "chains: []\n",
        encoding="utf-8",
    )


def test_missing_scheduler_block_preserves_v010_defaults(tmp_path: Path) -> None:
    path = tmp_path / "policies.yaml"
    _minimal_policy(path)

    policy = load_scheduler_policy(path)

    assert policy.declared is False
    assert policy.browsing.attempts == 3
    assert policy.browsing.reserve_successes == 2
    assert policy.history.min_runs == 2
    assert policy.history.min_success_ema == 0.8
    assert policy.ai_cache.pass_ttl_seconds == 21600
    assert policy.ai_cache.failure_ttl_seconds == 3600


def test_declared_scheduler_values_are_loaded_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "policies.yaml"
    _minimal_policy(
        path,
        "scheduler:\n"
        "  browsing: {attempts: 5, reserve_successes: 4}\n"
        "  history: {min_runs: 4, min_success_ema: 0.9, max_age_seconds: 86400}\n"
        "  ai_cache: {pass_ttl_seconds: 7200, failure_ttl_seconds: 900}\n",
    )

    policy = load_scheduler_policy(path)

    assert policy.declared is True
    assert policy.browsing.attempts == 5
    assert policy.browsing.reserve_successes == 4
    assert policy.history == HistorySchedulerPolicy(
        min_runs=4,
        min_success_ema=0.9,
        max_age_seconds=86400,
    )
    assert policy.ai_cache.pass_ttl_seconds == 7200
    assert policy.ai_cache.failure_ttl_seconds == 900


def test_invalid_reserve_threshold_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "policies.yaml"
    _minimal_policy(path, "scheduler:\n  browsing: {attempts: 3, reserve_successes: 4}\n")

    with pytest.raises(ValidationError, match="reserve_successes"):
        load_scheduler_policy(path)


def test_custom_history_thresholds_only_demote_current_stable_names() -> None:
    key = derive_fingerprint_key("token")
    history = {
        "version": 2,
        "cohort": {"runs": 5, "latency_ema_ms": 150.0, "last_seen_epoch": 100},
        "nodes": {
            fingerprint_runtime_name("keep", key): {
                "runs": 4,
                "success_ema": 0.95,
                "consecutive_failed_runs": 0,
                "last_seen_epoch": 100,
            },
            fingerprint_runtime_name("demote", key): {
                "runs": 4,
                "success_ema": 0.85,
                "consecutive_failed_runs": 0,
                "last_seen_epoch": 100,
            },
            fingerprint_runtime_name("not-live-stable", key): {
                "runs": 10,
                "success_ema": 1.0,
                "consecutive_failed_runs": 0,
                "last_seen_epoch": 100,
            },
        },
    }
    policy = HistorySchedulerPolicy(min_runs=3, min_success_ema=0.9, max_age_seconds=3600)

    preferred = preferred_stable_names_from_policy(
        {"keep", "demote"}, history, key, policy, now_epoch=120
    )

    assert preferred == {"keep"}
    assert "not-live-stable" not in preferred


def test_canonical_scheduler_block_preserves_current_production_semantics(repo_root: Path) -> None:
    policy = load_scheduler_policy(repo_root / "policies.yaml")

    assert policy.declared is True
    assert policy.browsing.attempts == 3
    assert policy.browsing.reserve_successes == 2
    assert policy.history.min_runs == 2
    assert policy.history.min_success_ema == 0.8
    assert policy.history.max_age_seconds == 2592000
    assert policy.ai_cache.pass_ttl_seconds == 21600
    assert policy.ai_cache.failure_ttl_seconds == 3600
