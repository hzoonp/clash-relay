from __future__ import annotations

from pathlib import Path

import yaml

from clash_relay.config_loader import load_project
from clash_relay.policy_document import load_policy_document
from clash_relay.routing_policy_v2 import load_routing_policy_v2
from clash_relay.routing_shadow import routing_drift_summary


def _project(repo_root: Path):
    return load_project(
        config_path=repo_root / "config.yaml",
        subscriptions_path=repo_root / "subscriptions.yaml",
        policies_path=repo_root / "policies.yaml",
    )


def test_final_routing_v2_permission_and_region_contract(repo_root: Path) -> None:
    project = _project(repo_root)
    policy = load_routing_policy_v2(project.policies)

    assert policy.declared is True
    assert {name: row.source_use for name, row in policy.scenarios.items()} == {
        "direct": "general",
        "general": "general",
        "browsing": "browsing",
        "media": "general",
        "download": "general",
        "ai": "ai",
        "final": "general",
    }
    assert policy.download.mode == "general_auto"
    assert policy.ai.excluded_regions == ("HK",)
    assert policy.ai.preferred_regions == ("US", "SG", "JP", "TW", "KR", "OTHER")

    subscription_1 = next(item for item in project.subscriptions if item.id == "subscription_1")
    assert subscription_1.allowed_uses == frozenset({"browsing", "ai"})


def test_final_routing_v2_presentation_and_acl_compatibility(repo_root: Path) -> None:
    manifest = yaml.safe_load((repo_root / "rules" / "acl4ssr.yaml").read_text(encoding="utf-8"))
    groups = {row["display_name"]: row for row in manifest["groups"]}

    visible = {name for name, row in groups.items() if not row.get("hidden", False)}
    assert visible == {
        "代理选择",
        "网页浏览",
        "人工智能",
        "流媒体",
        "消息通讯",
        "下载流量",
    }

    assert groups["媒体自动"]["hidden"] is True
    assert groups["媒体自动"]["provider_pool"] == "general"
    assert groups["通讯自动"]["hidden"] is True
    assert groups["通讯自动"]["provider_pool"] == "general"
    assert groups["下载自动"]["hidden"] is True
    assert groups["下载自动"]["provider_pool"] == "general"
    assert groups["网页自动"]["provider_pool"] == "browsing"
    assert groups["流媒体"]["members"][0] == {"group": "媒体自动"}
    assert groups["消息通讯"]["members"][0] == {"group": "通讯自动"}
    assert groups["下载流量"]["members"][0] == {"group": "下载自动"}

    assert groups["全球直连"]["members"] == [
        {"builtin": "DIRECT"},
        {"group": "代理选择"},
        {"group": "自动选择"},
    ]
    assert groups["广告拦截"]["members"] == [
        {"builtin": "REJECT"},
        {"builtin": "DIRECT"},
    ]
    assert groups["谷歌FCM"]["members"] == [
        {"group": "代理选择"},
        {"group": "全球直连"},
        {"group": "自动选择"},
    ]
    assert groups["微软服务"]["members"] == [
        {"group": "全球直连"},
        {"group": "代理选择"},
    ]
    assert groups["苹果服务"]["members"] == [
        {"group": "代理选择"},
        {"group": "全球直连"},
    ]
    assert groups["漏网之鱼"]["members"] == [
        {"group": "代理选择"},
        {"group": "全球直连"},
        {"group": "自动选择"},
    ]
    assert "应用净化" not in groups
    assert "奈飞视频" not in groups
    assert "奈飞节点" not in groups


def test_final_routing_v2_classification_order_and_targets(repo_root: Path) -> None:
    manifest = yaml.safe_load((repo_root / "rules" / "acl4ssr.yaml").read_text(encoding="utf-8"))
    sources = {row["id"]: row for row in manifest["sources"]}
    inline = {row["id"]: row for row in manifest["inline_rules"]}

    assert sources["proxy_lite"]["target"] == "网页浏览"
    assert sources["proxy_media"]["target"] == "流媒体"
    assert sources["download"]["target"] == "下载流量"
    assert sources["telegram"]["target"] == "消息通讯"
    assert sources["microsoft"]["target"] == "微软服务"
    assert sources["apple"]["target"] == "苹果服务"
    assert sources["google_fcm"]["target"] == "谷歌FCM"
    assert manifest["final_target"] == "漏网之鱼"
    assert "proxy_gfwlist" not in sources
    assert "youtube" not in sources
    assert "netflix" not in sources

    assert (
        sources["telegram"]["priority"]
        < sources["ai"]["priority"]
        < sources["proxy_media"]["priority"]
    )
    assert (
        sources["telegram"]["priority"]
        < sources["openai"]["priority"]
        < sources["proxy_media"]["priority"]
    )
    assert (
        sources["proxy_media"]["priority"]
        < sources["download"]["priority"]
        < sources["proxy_lite"]["priority"]
        < sources["china_domain"]["priority"]
        < sources["china_company_ip"]["priority"]
        < inline["geoip_cn"]["priority"]
    )


def test_final_routing_v2_ai_inventory_has_no_hong_kong(repo_root: Path) -> None:
    policies = load_policy_document(repo_root / "policies.yaml").document
    ai_pools = [row for row in policies["pools"] if row["source_use"] == "ai"]

    assert ai_pools
    assert all("HK" not in row["regions"] for row in ai_pools)
    assert {region for row in ai_pools for region in row["regions"]} == {
        "US",
        "SG",
        "JP",
        "TW",
        "KR",
        "OTHER",
    }


def test_final_routing_v2_drift_guard_is_healthy(repo_root: Path) -> None:
    summary = routing_drift_summary(_project(repo_root))

    assert summary["status"] == "healthy"
    assert summary["download"]["scheduler_applied"] is True
    assert summary["media"]["scheduler_applied"] is True
    assert summary["messaging"]["scheduler_applied"] is True
    assert summary["ai"]["policy_applied"] is True
    assert summary["foreign_web"]["classifier"] == "ProxyLite"
    assert summary["foreign_web"]["classifier_widened"] is False
    assert summary["acl4ssr_fidelity"]["compatibility_selectors_applied"] is True
    assert summary["acl4ssr_fidelity"]["classification_order_applied"] is True
    assert summary["acl4ssr_fidelity"]["ban_program_ad_disabled"] is True


def test_shadow_era_state_names_are_removed_from_drift_guard(repo_root: Path) -> None:
    workflow = (repo_root / ".github" / "workflows" / "routing-shadow.yml").read_text(
        encoding="utf-8"
    )
    implementation = (repo_root / "src" / "clash_relay" / "routing_shadow.py").read_text(
        encoding="utf-8"
    )

    assert "Routing V2 Drift Guard" in workflow
    assert "current_mode" not in workflow
    assert "cutover_mode" not in workflow
    assert "region_order_would_change" not in workflow
    assert "current_mode" not in implementation
    assert "cutover_mode" not in implementation
    assert "region_order_would_change" not in implementation
