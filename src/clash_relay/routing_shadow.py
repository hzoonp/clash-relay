"""Privacy-safe configuration-graph drift analysis for Routing Model V2."""

from __future__ import annotations

from typing import Any

from .config_loader import ProjectDefinition
from .policy_contract import RuntimePolicyContract, load_policy_contract
from .routing_model import compile_routing_model
from .routing_policy_v2 import load_routing_policy_v2


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


def _priority_contract_applied(
    by_source: dict[str, dict[str, Any]], contract: RuntimePolicyContract
) -> bool:
    """Validate declared ordering without inventing a second ordering model.

    The OpenAI binding is optional in legacy/pre-extension declarations.  Every
    other edge is required; optional edges are enforced whenever OpenAI is
    materialized.
    """

    required_ids = {
        source_id
        for edge in contract.priority_edges
        if "openai" not in edge
        for source_id in edge
    }
    if not required_ids <= set(by_source):
        return False

    for before, after in contract.priority_edges:
        if "openai" in (before, after) and (before not in by_source or after not in by_source):
            continue
        if before not in by_source or after not in by_source:
            return False
        if int(by_source[before]["priority"]) >= int(by_source[after]["priority"]):
            return False
    return True


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
    contract = load_policy_contract(project.policies)

    bindings = model["bindings"]
    by_source = {str(row["source_id"]): row for row in bindings}
    media_rules = sum(1 for row in bindings if row["scenario"] == "media")
    messaging_rules = sum(
        1 for row in bindings if row["scenario"] == "general" and row.get("service") == "telegram"
    )
    download_rules = sum(1 for row in bindings if row["scenario"] == "download")
    browsing_rules = sum(1 for row in bindings if row["scenario"] == "browsing")

    groups = {
        str(row["display_name"]): row
        for row in project.acl4ssr.get("groups", [])
        if isinstance(row, dict) and isinstance(row.get("display_name"), str)
    }
    ai_group = groups.get(contract.public_group("ai"))
    current_codes: list[str] = []
    if isinstance(ai_group, dict):
        for member in ai_group.get("members", []):
            name = member.get("group") if isinstance(member, dict) else None
            if not isinstance(name, str):
                continue
            region = contract.ai.region_for_display(name)
            if region is not None:
                current_codes.append(region)

    media_name = contract.public_group("media")
    messaging_name = contract.public_group("messaging")
    download_name = contract.public_group("download")
    browsing_name = contract.public_group("browsing")

    media_members = _declared_members(groups.get(media_name))
    messaging_members = _declared_members(groups.get(messaging_name))
    download_members = _declared_members(groups.get(download_name))
    media_applied = (
        bool(media_members)
        and media_members[0] == contract.automatic_group("media")
        and by_source.get("proxy_media", {}).get("target") == contract.binding_target("proxy_media")
        and "youtube" not in by_source
        and "netflix" not in by_source
    )
    messaging_applied = (
        bool(messaging_members)
        and messaging_members[0] == contract.automatic_group("messaging")
        and by_source.get("telegram", {}).get("target") == contract.binding_target("telegram")
    )
    download_applied = (
        policy.download.mode == "general_auto"
        and bool(download_members)
        and download_members[0] == contract.automatic_group("download")
        and by_source.get("download", {}).get("target") == contract.binding_target("download")
    )
    browsing_applied = (
        browsing_rules == 1
        and by_source.get("proxy_lite", {}).get("target") == contract.binding_target("proxy_lite")
        and "proxy_gfwlist" not in by_source
    )
    disabled_groups_applied = all(name not in groups for name in contract.disabled_groups)
    compatibility_applied = disabled_groups_applied and all(
        _declared_members(groups.get(name)) == list(expected)
        for name, expected in contract.compatibility_selectors.items()
    )
    order_applied = _priority_contract_applied(by_source, contract)

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
            "disabled_groups_applied": disabled_groups_applied,
            "ban_program_ad_disabled": disabled_groups_applied,
            "intentional_extensions": ["ai", "openai", "download"],
        },
        "ai": {
            "region_order": current_codes,
            "declared_region_order": list(policy.ai.preferred_regions),
            "excluded_regions": list(policy.ai.excluded_regions),
            "policy_applied": ai_applied,
        },
    }
