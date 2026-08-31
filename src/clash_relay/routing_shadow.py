"""Privacy-safe configuration-graph shadow analysis for Routing Model V2."""

from __future__ import annotations

from typing import Any

from .config_loader import ProjectDefinition
from .routing_model import compile_routing_model
from .routing_policy_v2 import load_routing_policy_v2

_MEDIA_AUTO_SERVICES = frozenset({"youtube", "foreign_media"})


def routing_shadow_summary(project: ProjectDefinition) -> dict[str, Any]:
    """Describe prospective cutover changes without inspecting runtime traffic.

    This is intentionally a declaration/config-graph shadow, not traffic
    telemetry. It never records domains, node names, addresses, credentials, or
    subscription URLs.
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
    download_rules = sum(1 for row in bindings if row["scenario"] == "download")
    browsing_rules = sum(1 for row in bindings if row["scenario"] == "browsing")

    ai_group = next(
        (
            row
            for row in project.acl4ssr.get("groups", [])
            if str(row.get("display_name")) == "人工智能"
        ),
        None,
    )
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

    return {
        "status": "shadow",
        "model_version": 2,
        "scenario_counts": dict(model["scenario_counts"]),
        "foreign_web": {
            "explicit_rule_sources": browsing_rules,
            "classifier_widened": False,
        },
        "download": {
            "current_mode": policy.download.mode,
            "cutover_mode": "general_auto",
            "affected_rule_sources": download_rules,
        },
        "media": {
            "auto_scheduler_candidate_rule_sources": media_auto,
        },
        "ai": {
            "current_region_order": current_codes,
            "cutover_region_order": list(policy.ai.preferred_regions),
            "excluded_regions": list(policy.ai.excluded_regions),
            "region_order_would_change": current_codes != list(policy.ai.preferred_regions),
        },
    }
