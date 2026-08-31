from __future__ import annotations

import json
import re
from pathlib import Path

from clash_relay.scheduler_history import (
    apply_history_preference,
    derive_fingerprint_key,
    empty_history,
    fingerprint_runtime_name,
    parse_history_bytes,
    preferred_stable_names,
    update_history,
)
from clash_relay.util import load_yaml_file


def test_scheduler_fingerprint_is_deterministic_and_opaque() -> None:
    token = "private-cloudflare-token"
    name = "[BROWSING:ANY] subscription_1/Secret Node"
    key = derive_fingerprint_key(token)
    fingerprint = fingerprint_runtime_name(name, key)

    assert fingerprint == fingerprint_runtime_name(name, key)
    assert len(fingerprint) == 64
    assert name not in fingerprint
    assert "subscription_1" not in fingerprint
    assert token not in fingerprint


def test_invalid_history_safely_degrades_to_empty() -> None:
    history, status = parse_history_bytes(b'{"version":999,"nodes":{"SECRET":{}}}')
    assert status == "invalid"
    assert history == empty_history()
    assert history["version"] == 2
    assert "SECRET" not in json.dumps(history)


def test_v1_history_is_migrated_without_changing_anonymous_fingerprints() -> None:
    key = derive_fingerprint_key("token")
    fingerprint = fingerprint_runtime_name("node", key)
    legacy = json.dumps(
        {
            "version": 1,
            "nodes": {
                fingerprint: {
                    "runs": 3,
                    "success_ema": 0.9,
                    "consecutive_failed_runs": 0,
                    "last_seen_epoch": 100,
                }
            },
        }
    ).encode()

    history, status = parse_history_bytes(legacy)

    assert status == "migrated"
    assert history["version"] == 2
    assert set(history["nodes"]) == {fingerprint}
    assert history["cohort"]["latency_ema_ms"] is None


def test_history_demotes_only_established_fresh_unstable_stable_nodes() -> None:
    key = derive_fingerprint_key("token")
    stable = {"good", "bad", "new"}
    history = {
        "version": 2,
        "cohort": {"runs": 4, "latency_ema_ms": 150.0, "last_seen_epoch": 100},
        "nodes": {
            fingerprint_runtime_name("good", key): {
                "runs": 4,
                "success_ema": 0.95,
                "consecutive_failed_runs": 0,
                "last_seen_epoch": 100,
            },
            fingerprint_runtime_name("bad", key): {
                "runs": 4,
                "success_ema": 0.70,
                "consecutive_failed_runs": 1,
                "last_seen_epoch": 100,
            },
        },
    }

    assert preferred_stable_names(stable, history, key, now_epoch=120) == {"good", "new"}


def test_stale_history_cannot_demote_a_current_stable_node() -> None:
    key = derive_fingerprint_key("token")
    history = {
        "version": 2,
        "cohort": {"runs": 5, "latency_ema_ms": 200.0, "last_seen_epoch": 1},
        "nodes": {
            fingerprint_runtime_name("stable-now", key): {
                "runs": 9,
                "success_ema": 0.1,
                "consecutive_failed_runs": 5,
                "last_seen_epoch": 1,
            }
        },
    }

    assert preferred_stable_names(
        {"stable-now"}, history, key, now_epoch=40 * 24 * 60 * 60
    ) == {"stable-now"}


def test_history_update_contains_no_runtime_names_and_tracks_cohort_latency() -> None:
    key = derive_fingerprint_key("token")
    updated = update_history(
        empty_history(),
        all_names={"stable-secret", "reserve-secret", "failed-secret"},
        qualified_names={"stable-secret", "reserve-secret"},
        stable_names={"stable-secret"},
        fingerprint_key=key,
        cohort_latency_ms=180.0,
        now_epoch=1000,
    )
    serialized = json.dumps(updated, sort_keys=True)

    assert updated["version"] == 2
    assert len(updated["nodes"]) == 3
    assert "stable-secret" not in serialized
    assert "reserve-secret" not in serialized
    assert "failed-secret" not in serialized
    records = list(updated["nodes"].values())
    assert sorted(record["success_ema"] for record in records) == [0.0, 0.666667, 1.0]
    assert updated["cohort"] == {
        "runs": 1,
        "latency_ema_ms": 180.0,
        "last_seen_epoch": 1000,
    }

    next_history = update_history(
        updated,
        all_names={"stable-secret"},
        qualified_names={"stable-secret"},
        stable_names={"stable-secret"},
        fingerprint_key=key,
        cohort_latency_ms=220.0,
        now_epoch=1100,
    )
    assert next_history["cohort"]["runs"] == 2
    assert next_history["cohort"]["latency_ema_ms"] == 190.0


def test_history_preference_narrows_auto_group_but_not_manual_provider(tmp_path: Path) -> None:
    candidate = tmp_path / "config.yaml"
    candidate.write_text(
        "mixed-port: 7890\n"
        "mode: rule\n"
        "proxy-providers:\n"
        "  cr_browsing_any:\n"
        "    type: inline\n"
        "    health-check:\n"
        "      enable: true\n"
        "      url: https://www.gstatic.com/generate_204\n"
        "      interval: 180\n"
        "      timeout: 3000\n"
        "      lazy: false\n"
        "      expected-status: '204'\n"
        "    payload:\n"
        "      - {name: preferred-a, type: direct}\n"
        "      - {name: preferred-b, type: direct}\n"
        "      - {name: preferred-c, type: direct}\n"
        "      - {name: historical-bad, type: direct}\n"
        "      - {name: reserve, type: direct}\n"
        "proxy-groups:\n"
        "  - name: Browsing Auto\n"
        "    type: url-test\n"
        "    hidden: true\n"
        "    use: [cr_browsing_any]\n"
        "    filter: '^(preferred-a|preferred-b|preferred-c|historical-bad)$'\n"
        "    url: http://www.gstatic.com/generate_204\n"
        "    interval: 300\n"
        "  - name: Browsing Manual\n"
        "    type: select\n"
        "    proxies: [Browsing Auto, DIRECT]\n"
        "rules:\n"
        "  - MATCH,Browsing Manual\n",
        encoding="utf-8",
    )

    rewrites = apply_history_preference(candidate, {"preferred-a", "preferred-b", "preferred-c"})
    config = load_yaml_file(candidate)
    auto_filter = config["proxy-groups"][0]["filter"]

    assert rewrites == 1
    assert re.fullmatch(auto_filter, "preferred-a")
    assert re.fullmatch(auto_filter, "preferred-b")
    assert re.fullmatch(auto_filter, "preferred-c")
    assert re.fullmatch(auto_filter, "historical-bad") is None
    assert re.fullmatch(auto_filter, "reserve") is None
    assert len(config["proxy-providers"]["cr_browsing_any"]["payload"]) == 5
    assert config["proxy-groups"][1]["proxies"] == ["Browsing Auto", "DIRECT"]


def test_history_preference_keeps_current_stable_filter_when_pool_is_too_small(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "config.yaml"
    original = (
        "proxy-providers:\n"
        "  cr_browsing_any:\n"
        "    type: inline\n"
        "    payload:\n"
        "      - {name: stable-a, type: direct}\n"
        "      - {name: stable-b, type: direct}\n"
        "      - {name: reserve, type: direct}\n"
        "proxy-groups:\n"
        "  - name: Browsing Auto\n"
        "    type: url-test\n"
        "    use: [cr_browsing_any]\n"
        "    filter: '^(stable-a|stable-b)$'\n"
        "    url: http://www.gstatic.com/generate_204\n"
        "    interval: 300\n"
        "rules: [MATCH,Browsing Auto]\n"
    )
    candidate.write_text(original, encoding="utf-8")

    assert apply_history_preference(candidate, {"stable-a", "stable-b"}) == 0
    assert candidate.read_text(encoding="utf-8") == original
