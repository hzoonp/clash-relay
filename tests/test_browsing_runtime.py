from __future__ import annotations

from pathlib import Path

import pytest

from clash_relay.browsing_regions import (
    region_display_name,
    region_reserve_group,
    region_stable_group,
)
from clash_relay.browsing_runtime import (
    BROWSING_AUTO_GROUP,
    BROWSING_PUBLIC_GROUP,
    apply_browsing_history_preference,
    filter_matches,
    harden_browsing_runtime,
    rewrite_hardened_browsing_qualified_candidate,
    validate_browsing_public_surface,
)
from clash_relay.errors import ValidationError
from clash_relay.util import dump_yaml, load_yaml_file


def _policies(regions: list[str] | None = None) -> dict:
    preferred = regions or ["US", "SG", "JP"]
    return {
        "scheduler": {
            "browsing": {
                "attempts": 3,
                "reserve_successes": 2,
                "region_switch_interval": 300,
            }
        },
        "routing": {"browsing": {"preferred_regions": preferred}},
        "pools": [
            {
                "id": "browsing",
                "probe": "browsing",
                "regions": preferred,
                "fallback_order": preferred,
            }
        ],
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


def _provider(*names: str) -> dict:
    return {
        "type": "inline",
        "health-check": {
            "enable": True,
            "url": "https://old.invalid/generate_204",
            "interval": 999,
            "timeout": 9999,
            "lazy": True,
            "expected-status": "204",
        },
        "payload": [_proxy(name) for name in names],
    }


def _candidate() -> dict:
    return {
        "mixed-port": 7890,
        "mode": "rule",
        "proxy-providers": {
            "cr_browsing_us": _provider(
                "us-stable-a",
                "us-stable-b",
                "us-stable-c",
                "us-stable-bad",
                "us-reserve",
                "us-drop",
            ),
            "cr_browsing_sg": _provider("sg-drop"),
            "cr_browsing_jp": _provider("jp-stable", "jp-reserve", "jp-drop"),
        },
        "proxy-groups": [
            {
                "name": BROWSING_AUTO_GROUP,
                "type": "url-test",
                "hidden": True,
                "use": ["cr_browsing_us", "cr_browsing_sg", "cr_browsing_jp"],
                "filter": ".*",
                "url": "http://www.gstatic.com/generate_204",
                "interval": 180,
                "tolerance": 150,
            },
            {
                "name": BROWSING_PUBLIC_GROUP,
                "type": "select",
                "proxies": [BROWSING_AUTO_GROUP, "DIRECT"],
                "use": ["cr_browsing_us", "cr_browsing_sg", "cr_browsing_jp"],
                "filter": ".*",
            },
        ],
        "rules": [f"MATCH,{BROWSING_PUBLIC_GROUP}"],
    }


def _groups(config: dict) -> dict[str, dict]:
    return {group["name"]: group for group in config["proxy-groups"]}


def test_hardening_builds_region_priority_surface_and_unifies_https_probe() -> None:
    config = _candidate()

    report = harden_browsing_runtime(config, _policies())
    groups = _groups(config)
    public = groups[BROWSING_PUBLIC_GROUP]
    automatic = groups[BROWSING_AUTO_GROUP]

    assert report["status"] == "regional_hardened"
    assert report["preferred_regions"] == ["US", "SG", "JP"]
    assert report["available_regions"] == ["US", "SG", "JP"]
    assert public == {
        "name": BROWSING_PUBLIC_GROUP,
        "type": "select",
        "proxies": [
            BROWSING_AUTO_GROUP,
            region_display_name("US"),
            region_display_name("SG"),
            region_display_name("JP"),
            "DIRECT",
        ],
    }
    assert automatic["type"] == "fallback"
    assert automatic["hidden"] is True
    assert automatic["proxies"] == [
        region_display_name("US"),
        region_display_name("SG"),
        region_display_name("JP"),
    ]
    assert automatic["interval"] == 300
    assert automatic["url"] == "https://www.gstatic.com/generate_204"

    for region in ("US", "SG", "JP"):
        regional = groups[region_display_name(region)]
        assert regional["hidden"] is True
        assert regional["type"] == "fallback"
        assert regional["proxies"] == [
            region_stable_group(region),
            region_reserve_group(region),
        ]
        for tier_name in (region_stable_group(region), region_reserve_group(region)):
            tier = groups[tier_name]
            assert tier["hidden"] is True
            assert tier["type"] == "url-test"
            assert tier["use"] == [f"cr_browsing_{region.lower()}"]
            assert tier["url"] == "https://www.gstatic.com/generate_204"
            assert tier["timeout"] == 3000
            assert tier["expected-status"] == 204

    health = config["proxy-providers"]["cr_browsing_us"]["health-check"]
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
    _groups(config)[BROWSING_PUBLIC_GROUP]["use"] = ["cr_browsing_us"]

    with pytest.raises(ValidationError, match="must not expose proxy providers"):
        validate_browsing_public_surface(config)


def test_qualification_splits_each_region_and_removes_empty_region(tmp_path: Path) -> None:
    config = _candidate()
    harden_browsing_runtime(config, _policies())
    candidate = tmp_path / "config.yaml"
    candidate.write_text(dump_yaml(config), encoding="utf-8")

    qualified = {
        "us-stable-a",
        "us-stable-b",
        "us-stable-c",
        "us-reserve",
        "jp-stable",
        "jp-reserve",
    }
    stable = {"us-stable-a", "us-stable-b", "us-stable-c", "jp-stable"}
    report = rewrite_hardened_browsing_qualified_candidate(candidate, qualified, stable)
    rewritten = load_yaml_file(candidate)
    groups = _groups(rewritten)

    assert "cr_browsing_sg" not in rewritten["proxy-providers"]
    assert region_display_name("SG") not in groups
    assert report["available_regions"] == ["US", "JP"]
    assert report["removed_regions"] == ["SG"]
    assert groups[BROWSING_AUTO_GROUP]["proxies"] == [
        region_display_name("US"),
        region_display_name("JP"),
    ]
    assert groups[BROWSING_PUBLIC_GROUP]["proxies"] == [
        BROWSING_AUTO_GROUP,
        region_display_name("US"),
        region_display_name("JP"),
        "DIRECT",
    ]

    us_stable = groups[region_stable_group("US")]["filter"]
    us_reserve = groups[region_reserve_group("US")]["filter"]
    assert filter_matches(us_stable, "us-stable-a")
    assert not filter_matches(us_reserve, "us-stable-a")
    assert filter_matches(us_reserve, "us-reserve")

    jp_stable = groups[region_stable_group("JP")]["filter"]
    jp_reserve = groups[region_reserve_group("JP")]["filter"]
    assert filter_matches(jp_stable, "jp-stable")
    assert filter_matches(jp_reserve, "jp-reserve")
    assert report["regions"]["US"]["stable"] == 3
    assert report["regions"]["JP"]["reserve"] == 1
    assert report["regions"]["SG"]["qualified"] == 0
    assert report["regional_scheduling"] is True


def test_history_demotion_is_region_local_and_moves_node_to_same_region_reserve(
    tmp_path: Path,
) -> None:
    config = _candidate()
    harden_browsing_runtime(config, _policies())
    candidate = tmp_path / "config.yaml"
    candidate.write_text(dump_yaml(config), encoding="utf-8")

    qualified = {
        "us-stable-a",
        "us-stable-b",
        "us-stable-c",
        "us-stable-bad",
        "us-reserve",
        "jp-stable",
        "jp-reserve",
    }
    stable = {
        "us-stable-a",
        "us-stable-b",
        "us-stable-c",
        "us-stable-bad",
        "jp-stable",
    }
    rewrite_hardened_browsing_qualified_candidate(candidate, qualified, stable)

    rewrites = apply_browsing_history_preference(
        candidate,
        preferred_names={"us-stable-a", "us-stable-b", "us-stable-c", "jp-stable"},
        stable_names=stable,
        qualified_names=qualified,
    )
    rewritten = load_yaml_file(candidate)
    groups = _groups(rewritten)

    assert rewrites == 2
    us_stable = groups[region_stable_group("US")]["filter"]
    us_reserve = groups[region_reserve_group("US")]["filter"]
    assert not filter_matches(us_stable, "us-stable-bad")
    assert filter_matches(us_reserve, "us-stable-bad")
    assert filter_matches(us_reserve, "us-reserve")

    jp_stable = groups[region_stable_group("JP")]["filter"]
    assert filter_matches(jp_stable, "jp-stable")
    assert groups[region_display_name("US")]["proxies"] == [
        region_stable_group("US"),
        region_reserve_group("US"),
    ]
    assert region_stable_group("JP") not in groups[region_display_name("US")]["proxies"]
