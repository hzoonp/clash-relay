from __future__ import annotations

from pathlib import Path

import pytest

from clash_relay.browsing_runtime import (
    BROWSING_AUTO_GROUP,
    BROWSING_PUBLIC_GROUP,
    BROWSING_RESERVE_GROUP,
    BROWSING_STABLE_GROUP,
    apply_browsing_history_preference,
    filter_matches,
    harden_browsing_runtime,
    rewrite_hardened_browsing_qualified_candidate,
    validate_browsing_public_surface,
)
from clash_relay.errors import ValidationError
from clash_relay.util import dump_yaml, load_yaml_file


def _policies() -> dict:
    return {
        "pools": [{"id": "browsing", "probe": "browsing"}],
        "probes": {
            "browsing": {
                "url": "https://www.gstatic.com/generate_204",
                "method": "HEAD",
                "expected_status": "204",
                "interval": 180,
                "timeout": 3000,
                "lazy": False,
                "tolerance": 150,
            }
        },
    }


def _proxy(name: str) -> dict:
    return {"name": name, "type": "direct"}


def _candidate() -> dict:
    return {
        "mixed-port": 7890,
        "mode": "rule",
        "proxy-providers": {
            "cr_browsing_any": {
                "type": "inline",
                "health-check": {
                    "enable": True,
                    "url": "https://old.invalid/generate_204",
                    "interval": 999,
                    "timeout": 9999,
                    "lazy": True,
                    "expected-status": "204",
                },
                "payload": [
                    _proxy("stable-a"),
                    _proxy("stable-b"),
                    _proxy("stable-c"),
                    _proxy("stable-bad"),
                    _proxy("reserve-a"),
                    _proxy("drop"),
                ],
            }
        },
        "proxy-groups": [
            {
                "name": BROWSING_AUTO_GROUP,
                "type": "url-test",
                "hidden": True,
                "use": ["cr_browsing_any"],
                "filter": ".*",
                "url": "http://www.gstatic.com/generate_204",
                "interval": 180,
                "tolerance": 150,
            },
            {
                "name": BROWSING_PUBLIC_GROUP,
                "type": "select",
                "proxies": [BROWSING_AUTO_GROUP, "DIRECT"],
                "use": ["cr_browsing_any"],
                "filter": ".*",
            },
        ],
        "rules": [f"MATCH,{BROWSING_PUBLIC_GROUP}"],
    }


def _groups(config: dict) -> dict[str, dict]:
    return {group["name"]: group for group in config["proxy-groups"]}


def test_hardening_removes_public_provider_exposure_and_unifies_https_probe() -> None:
    config = _candidate()

    report = harden_browsing_runtime(config, _policies())
    groups = _groups(config)
    public = groups[BROWSING_PUBLIC_GROUP]
    automatic = groups[BROWSING_AUTO_GROUP]

    assert report["status"] == "hardened"
    assert public == {
        "name": BROWSING_PUBLIC_GROUP,
        "type": "select",
        "proxies": [BROWSING_AUTO_GROUP, "DIRECT"],
    }
    assert automatic["type"] == "fallback"
    assert automatic["hidden"] is True
    assert automatic["proxies"] == [BROWSING_STABLE_GROUP, BROWSING_RESERVE_GROUP]
    assert automatic["url"] == "https://www.gstatic.com/generate_204"
    assert automatic["timeout"] == 3000
    assert automatic["expected-status"] == 204

    for name in (BROWSING_STABLE_GROUP, BROWSING_RESERVE_GROUP):
        tier = groups[name]
        assert tier["hidden"] is True
        assert tier["type"] == "url-test"
        assert tier["use"] == ["cr_browsing_any"]
        assert tier["url"] == "https://www.gstatic.com/generate_204"
        assert tier["timeout"] == 3000
        assert tier["expected-status"] == 204

    health = config["proxy-providers"]["cr_browsing_any"]["health-check"]
    assert health == {
        "enable": True,
        "url": "https://www.gstatic.com/generate_204",
        "interval": 180,
        "timeout": 3000,
        "lazy": False,
        "expected-status": 204,
    }
    validate_browsing_public_surface(config)


def test_public_surface_validator_rejects_provider_leak() -> None:
    config = _candidate()
    harden_browsing_runtime(config, _policies())
    _groups(config)[BROWSING_PUBLIC_GROUP]["use"] = ["cr_browsing_any"]

    with pytest.raises(ValidationError, match="must not expose proxy providers"):
        validate_browsing_public_surface(config)


def test_qualification_splits_stable_and_reserve_without_exposing_nodes(tmp_path: Path) -> None:
    config = _candidate()
    harden_browsing_runtime(config, _policies())
    candidate = tmp_path / "config.yaml"
    candidate.write_text(dump_yaml(config), encoding="utf-8")

    qualified = {"stable-a", "stable-b", "stable-c", "reserve-a"}
    stable = {"stable-a", "stable-b", "stable-c"}
    report = rewrite_hardened_browsing_qualified_candidate(candidate, qualified, stable)
    rewritten = load_yaml_file(candidate)
    groups = _groups(rewritten)

    assert [
        proxy["name"] for proxy in rewritten["proxy-providers"]["cr_browsing_any"]["payload"]
    ] == ["stable-a", "stable-b", "stable-c", "reserve-a"]
    stable_filter = groups[BROWSING_STABLE_GROUP]["filter"]
    reserve_filter = groups[BROWSING_RESERVE_GROUP]["filter"]
    for name in stable:
        assert filter_matches(stable_filter, name)
        assert not filter_matches(reserve_filter, name)
    assert filter_matches(reserve_filter, "reserve-a")
    assert not filter_matches(stable_filter, "reserve-a")
    assert groups[BROWSING_PUBLIC_GROUP].get("use") is None
    assert groups[BROWSING_PUBLIC_GROUP]["proxies"] == [BROWSING_AUTO_GROUP, "DIRECT"]
    assert report["automatic_failover"] is True
    assert report["stable_automatic_nodes"] == 3
    assert report["reserve_automatic_nodes"] == 1
    assert report["failed_nodes"] == 2


def test_history_demotion_moves_current_stable_node_into_reserve(tmp_path: Path) -> None:
    config = _candidate()
    harden_browsing_runtime(config, _policies())
    candidate = tmp_path / "config.yaml"
    candidate.write_text(dump_yaml(config), encoding="utf-8")

    qualified = {"stable-a", "stable-b", "stable-c", "stable-bad", "reserve-a"}
    stable = {"stable-a", "stable-b", "stable-c", "stable-bad"}
    rewrite_hardened_browsing_qualified_candidate(candidate, qualified, stable)

    rewrites = apply_browsing_history_preference(
        candidate,
        preferred_names={"stable-a", "stable-b", "stable-c"},
        stable_names=stable,
        qualified_names=qualified,
    )
    rewritten = load_yaml_file(candidate)
    groups = _groups(rewritten)
    stable_filter = groups[BROWSING_STABLE_GROUP]["filter"]
    reserve_filter = groups[BROWSING_RESERVE_GROUP]["filter"]

    assert rewrites == 2
    assert not filter_matches(stable_filter, "stable-bad")
    assert filter_matches(reserve_filter, "stable-bad")
    assert filter_matches(reserve_filter, "reserve-a")
    assert filter_matches(stable_filter, "stable-a")
    assert groups[BROWSING_PUBLIC_GROUP].get("use") is None
