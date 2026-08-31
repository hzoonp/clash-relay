"""Privacy-safe configuration-graph drift analysis for Routing Model V2."""

from __future__ import annotations

from typing import Any

from .config_loader import ProjectDefinition
from .routing_model import compile_routing_model
from .routing_policy_v2 import load_routing_policy_v2

_MEDIA_AUTO_SERVICES = frozenset({"youtube", "foreign_media"})


def _route_member(group: dict[str, Any] | None) -> str | None:
    if not isinstance(group, dict):
        return None
    route = group.get("route")
    if not isinstance(route, dict):
        return None
    member = route.get("member")
    if not isinstance(member, dict):
        return None
    if "group" in member:
        return str(member["group"])
    if "builtin" in member:
        return str(member["builtin"])
    return None


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
    media_auto = sum(
        1
        for row in bindings
        if row["scenario"] == "media" and row.get("service") in _MEDIA_AUTO_SERVICES
    )
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
    current_codes = [label_to_code[name] for name in current_ai_regions if name in label_to_code]

    media_members = _declared_members(groups.get("流媒体"))
    messaging_members = _declared_members(groups.get("消息通讯"))
    download_members = _declared_members(groups.get("下载流量"))
    media_applied = (
        bool(media_members)
        and media_members[0] == "媒体自动"
        and all(
            _route_member(groups.get(name)) == "流媒体" for name in ("油管视频", "国外媒体")
        )
    )
    messaging_applied = (
        bool(messaging_members)
        and messaging_members[0] == "通讯自动"
        and _route_member(groups.get("电报消息")) == "消息通讯"
    )
    download_applied = (
        policy.download.mode == "general_auto"
        and bool(download_members)
        and download_members[0] == "下载自动"
    )
    ai_applied = current_codes == list(policy.ai.preferred_regions)
    healthy = media_applied and messaging_applied and download_applied and ai_applied

    return {
        "status": "healthy" if healthy else "drifted",
        "model_version": 2,
        "scenario_counts": dict(model["scenario_counts"]),
        "foreign_web": {
            "explicit_rule_sources": browsing_rules,
            "classifier_widened": False,
        },
        "download": {
            "mode": policy.download.mode,
            "rule_sources": download_rules,
            "scheduler_applied": download_applied,
        },
        "media": {
            "auto_scheduler_rule_sources": media_auto,
            "scheduler_applied": media_applied,
        },
        "messaging": {
            "rule_sources": messaging_rules,
            "scheduler_applied": messaging_applied,
        },
        "ai": {
            "region_order": current_codes,
            "declared_region_order": list(policy.ai.preferred_regions),
            "excluded_regions": list(policy.ai.excluded_regions),
            "policy_applied": ai_applied,
        },
    }
