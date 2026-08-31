from __future__ import annotations

from pathlib import Path

import yaml

from clash_relay.acl4ssr_policy import apply_acl4ssr_group_semantics


def test_canonical_public_surface_contains_only_scenarios(repo_root: Path) -> None:
    manifest = yaml.safe_load((repo_root / "rules/acl4ssr.yaml").read_text(encoding="utf-8"))
    visible = {
        str(group["display_name"])
        for group in manifest["groups"]
        if not bool(group.get("hidden", False))
    }

    assert visible == {"代理选择", "网页浏览", "人工智能"}
    assert manifest["final_target"] == "漏网之鱼"
    assert next(group for group in manifest["groups"] if group["display_name"] == "漏网之鱼")[
        "hidden"
    ] is True


def test_ai_region_dimension_hard_excludes_hong_kong(repo_root: Path) -> None:
    policies = yaml.safe_load((repo_root / "policies.yaml").read_text(encoding="utf-8"))
    ai_pools = [pool for pool in policies["pools"] if pool["source_use"] == "ai"]

    assert ai_pools
    assert all("HK" not in pool["regions"] for pool in ai_pools)
    assert {region for pool in ai_pools for region in pool["regions"]} == {
        "SG",
        "JP",
        "US",
        "TW",
        "KR",
        "OTHER",
    }


def test_ai_pool_wrappers_are_hidden_scheduling_dimensions() -> None:
    output = {
        "proxy-providers": {
            "cr_ai_us_us": {
                "type": "inline",
                "payload": [],
            }
        },
        "proxy-groups": [
            {
                "name": "__CR_AUTO_AI_US_US",
                "type": "url-test",
                "hidden": True,
                "use": ["cr_ai_us_us"],
            },
            {
                "name": "AI · 美国",
                "type": "select",
                "proxies": ["__CR_AUTO_AI_US_US"],
            },
            {
                "name": "人工智能",
                "type": "select",
                "proxies": ["AI · 美国"],
            },
        ],
    }

    report = apply_acl4ssr_group_semantics(
        output,
        group_specs=[],
        pool_specs=[
            {
                "id": "ai_us",
                "display_name": "AI · 美国",
                "source_use": "ai",
            }
        ],
    )

    groups = {group["name"]: group for group in output["proxy-groups"]}
    assert groups["AI · 美国"]["hidden"] is True
    assert groups["人工智能"].get("hidden", False) is False
    assert report["hidden_inventories"] == ["AI · 美国"]
