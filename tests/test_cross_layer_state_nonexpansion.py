from __future__ import annotations

from clash_relay.ai_qualification_cache import cached_service_decisions
from clash_relay.node_policy import filter_proxies_by_multiplier
from clash_relay.scheduler_history import (
    derive_fingerprint_key,
    fingerprint_runtime_name,
    preferred_stable_names,
)


def test_multiplier_ceiling_keeps_unmarked_and_exactly_two_x_nodes() -> None:
    rows = [
        {"name": "US Standard"},
        {"name": "US x64"},
        {"name": "US Exactly 2x"},
        {"name": "US 2.01x"},
    ]

    kept, rejected = filter_proxies_by_multiplier(rows, max_multiplier=2.0)

    assert rejected == 1
    assert [row["name"] for row in kept] == [
        "US Standard",
        "US x64",
        "US Exactly 2x",
    ]


def test_scheduler_history_can_only_narrow_the_current_stable_set() -> None:
    key = derive_fingerprint_key("token")
    ghost = fingerprint_runtime_name("ghost-from-history", key)
    current = fingerprint_runtime_name("current-stable", key)
    history = {
        "version": 3,
        "cohort": {"runs": 5, "latency_ema_ms": 100.0, "last_seen_epoch": 100},
        "nodes": {
            ghost: {
                "runs": 10,
                "success_ema": 1.0,
                "consecutive_failed_runs": 0,
                "last_seen_epoch": 100,
                "historically_preferred": True,
            },
            current: {
                "runs": 10,
                "success_ema": 1.0,
                "consecutive_failed_runs": 0,
                "last_seen_epoch": 100,
                "historically_preferred": True,
            },
        },
    }

    preferred = preferred_stable_names({"current-stable"}, history, key, now_epoch=120)

    assert preferred == {"current-stable"}
    assert "ghost-from-history" not in preferred


def test_ai_cache_cannot_reintroduce_a_runtime_name_absent_from_current_candidate() -> None:
    current_fingerprint = "a" * 64
    ghost_fingerprint = "b" * 64
    fingerprints = {"current-runtime": current_fingerprint}
    cache = {
        "version": 1,
        "nodes": {
            current_fingerprint: {
                "services": {"ai_openai": {"passed": True, "checked_epoch": 1000}}
            },
            ghost_fingerprint: {"services": {"ai_openai": {"passed": True, "checked_epoch": 1000}}},
        },
    }

    passed, failed, live = cached_service_decisions(
        cache,
        fingerprints,
        "ai_openai",
        now_epoch=1001,
    )

    assert passed == {"current-runtime"}
    assert failed == set()
    assert live == set()
    assert "ghost-runtime" not in passed
