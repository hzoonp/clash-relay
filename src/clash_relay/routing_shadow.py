"""Privacy-safe configuration-graph drift analysis for Routing Model V2."""

from __future__ import annotations

from typing import Any

from .config_loader import ProjectDefinition
from .routing_model import compile_routing_model
from .routing_policy_v2 import load_routing_policy_v2

_ACL_COMPATIBILITY_MEMBERS = {
    "全球直连": ["DIRECT", "代理选择", "自动选择"],
    "广告拦截": ["REJECT", "DIRECT"],
    "谷歌FCM": ["代理选择", "全球直连", "自动选择"],
    "微软服务": ["全球直连", "代理选择"],
    "苹果服务": ["代理选择", "全球直连"],
    "漏网之鱼": ["代理选择", "全球直连", "自动选择"],
}


def _declared_members(group: dict[str, Any] | None) -> list[str]:
    if not isinstance(group, dict):
        return []
    result: list[str] = []
    for member in group.get("members", []):
        if not isinstance(member, dict):
            continue
        if "group" in member:
            result.append(str(member["group"]))
        elif "builtin" in member:
            result.append(str(member["builtin"]))
    return result


def routing_drift_summary(project: ProjectDefinition) -> dict[str, Any]:
    """Verify the finalized Routing V2 declaration/configuration graph.

    This is intentionally configuration-graph validation, not traffic
    telemetry. It never records domains, node names, addresses, credentials,
    subscription URLs, or user activity.
    """

    if project.acl4ssr is None:
        return {"status": "disabled"}
    model = compile_routing_model(project.acl4ssr)
    if model is None:
        return {"status": "disabled"}
    policy = load_routing_policy_v2(project.policies)

    bindings = model["bindings"]
    by_source = {str(row["source_id"]): row for row in bindings}
    media_rules = sum(1 for row in bindings if row["scenario"] == "media")
    messaging_rules = sum(
        1
        for row in bindings
        if row["scenario"] == "general" and row.get("service") == "telegram"
    )
    download_rules = sum(1 for row in bindings if row["scenario"] == "download")
    browsing_rules = sum(1 for row in bindings if row["scenario"] == "browsing")

    groups = {
        str(row["display_name"]): row
        for row in project.acl4ssr.get("groups", [])
        if isinstance(row, dict) and isinstance(row.get("display_name"), str)
    }
    ai_group = groups.get("人工智能")
    current_ai_regions: list[str] = []
    if isinstance(ai_group, dict):
        for member in ai_group.get("members", []):
            name = member.get("group") if isinstance(member, dict) else None
            if isinstance(name, str) and name.startswith("AI · "):
                current_ai_regions.append(name.removeprefix("AI · "))
    label_to_code = {
        "美国": "US",
        "新加坡": "SG",
        "日本": "JP",
        "台湾": "TW",
        "韩国": "KR",
        "其他地区": "OTHER",
        "香港": "HK",
    }
    current_codes = [
        label_to_code[name] for name in current_ai_regions if name in label_to_code
    ]

    media_members = _declared_members(groups.get("流媒体"))
    messaging_members = _declared_members(groups.get("消息通讯"))
    download_members = _declared_members(groups.get("下载流量"))
    media_applied = (
        bool(media_members)
        and media_members[0] == "媒体自动"
        and by_source.get("proxy_media", {}).get("target") == "流媒体"
        and "youtube" not in by_source
        and "netflix" not in by_source
    )
    messaging_applied = (
        bool(messaging_members)
        and messaging_members[0] == "通讯自动"
        and by_source.get("telegram", {}).get("target") == "消息通讯"
    )
    download_applied = (
        policy.download.mode == "general_auto"
        and bool(download_members)
        and download_members[0] == "下载自动"
        and by_source.get("download", {}).get("target") == "下载流量"
    )
    browsing_applied = (
        browsing_rules == 1
        and by_source.get("proxy_lite", {}).get("target") == "网页浏览"
        and "proxy_gfwlist" not in by_source
    )
    compatibility_applied = "应用净化" not in groups and all(
        _declared_members(groups.get(name)) == expected
        for name, expected in _ACL_COMPATIBILITY_MEMBERS.items()
    )

    required_order = [
        "telegram",
        "ai",
        "proxy_media",
        "download",
        "proxy_lite",
        "china_domain",
        "china_company_ip",
        "geoip_cn",
    ]
    openai_present = "openai" in by_source
    order_applied = all(source_id in by_source for source_id in required_order)
    if order_applied:
        priorities = {
            source_id: int(by_source[source_id]["priority"])
            for source_id in required_order
        }
        order_applied = (
            priorities["telegram"]
            < priorities["ai"]
            < priorities["proxy_media"]
            < priorities["download"]
            < priorities["proxy_lite"]
            < priorities["china_domain"]
            < priorities["china_company_ip"]
            < priorities["geoip_cn"]
        )
        if openai_present:
            order_applied = order_applied and (
                priorities["telegram"]
                < int(by_source["openai"]["priority"])
                < priorities["proxy_media"]
            )

    ai_applied = current_codes == list(policy.ai.preferred_regions)
    healthy = all(
        (
            media_applied,
            messaging_applied,
            download_applied,
            browsing_applied,
            compatibility_applied,
            order_applied,
            ai_applied,
        )
    )

    return {
        "status": "healthy" if healthy else "drifted",
        "model_version": 2,
        "scenario_counts": dict(model["scenario_counts"]),
        "foreign_web": {
            "explicit_rule_sources": browsing_rules,
            "classifier": "ProxyLite",
            "classifier_widened": False,
            "policy_applied": browsing_applied,
        },
        "download": {
            "mode": policy.download.mode,
            "rule_sources": download_rules,
            "scheduler_applied": download_applied,
        },
        "media": {
            "rule_sources": media_rules,
            "classifier": "ProxyMedia",
            "scheduler_applied": media_applied,
        },
        "messaging": {
            "rule_sources": messaging_rules,
            "classifier": "Telegram",
            "scheduler_applied": messaging_applied,
        },
        "acl4ssr_fidelity": {
            "compatibility_selectors_applied": compatibility_applied,
            "classification_order_applied": order_applied,
            "ban_program_ad_disabled": "应用净化" not in groups,
            "intentional_extensions": ["ai", "openai", "download"],
        },
        "ai": {
            "region_order": current_codes,
            "declared_region_order": list(policy.ai.preferred_regions),
            "excluded_regions": list(policy.ai.excluded_regions),
            "policy_applied": ai_applied,
        },
    }
