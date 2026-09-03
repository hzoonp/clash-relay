from __future__ import annotations

from pathlib import Path

import yaml

from clash_relay.policy_document import load_policy_document


def test_canonical_public_surface_contains_only_scenarios(repo_root: Path) -> None:
    manifest = yaml.safe_load((repo_root / "rules/acl4ssr.yaml").read_text(encoding="utf-8"))
    visible = {
        str(group["display_name"])
        for group in manifest["groups"]
        if not bool(group.get("hidden", False))
    }

    assert visible == {
        "代理选择",
        "网页浏览",
        "人工智能",
        "流媒体",
        "消息通讯",
        "下载流量",
    }
    assert manifest["final_target"] == "漏网之鱼"
    final_group = next(group for group in manifest["groups"] if group["display_name"] == "漏网之鱼")
    assert final_group["hidden"] is True


def test_ai_region_dimension_hard_excludes_hong_kong(repo_root: Path) -> None:
    policies = load_policy_document(repo_root / "policies.yaml").document
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
