"""Fail-closed Routing Model V2 audits over the generated Mihomo graph."""

from __future__ import annotations

from typing import Any

from .config_loader import ProjectDefinition
from .errors import ValidationError
from .routing_model import compile_routing_model
from .routing_policy_v2 import RoutingPolicyV2, load_routing_policy_v2, routing_policy_summary

_AI_SERVICE_PREFIXES = {
    "openai": "__CR_AI_OPENAI_",
    "claude": "__CR_AI_CLAUDE_",
    "gemini": "__CR_AI_GEMINI_",
}
_AI_SERVICE_TARGETS = {
    "openai": "__CR_AI_SERVICE_OPENAI",
    "claude": "__CR_AI_SERVICE_CLAUDE",
    "gemini": "__CR_AI_SERVICE_GEMINI",
}
_REGION_DISPLAY_NAMES = {
    "HK": ("AI · 香港", "AI · HK"),
    "TW": ("AI · 台湾", "AI · TW"),
    "SG": ("AI · 新加坡", "AI · SG"),
    "JP": ("AI · 日本", "AI · JP"),
    "US": ("AI · 美国", "AI · US"),
    "KR": ("AI · 韩国", "AI · KR"),
    "OTHER": ("AI · 其他地区", "AI · OTHER"),
}
_REGION_CANONICAL_DISPLAY = {
    region: names[0] for region, names in _REGION_DISPLAY_NAMES.items()
}


def _groups(candidate: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = candidate.get("proxy-groups")
    if not isinstance(rows, list):
        raise ValidationError("routing v2 audit requires proxy-groups")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise ValidationError("routing v2 audit found malformed proxy group")
        result[str(row["name"])] = row
    return result


def _group_proxies(groups: dict[str, dict[str, Any]], name: str) -> list[str]:
    group = groups.get(name)
    if not isinstance(group, dict):
        raise ValidationError(f"routing v2 required group {name!r} is missing")
    proxies = group.get("proxies")
    if not isinstance(proxies, list) or not all(isinstance(item, str) for item in proxies):
        raise ValidationError(f"routing v2 group {name!r} has invalid proxy references")
    return [str(item) for item in proxies]


def _expected_member(route: dict[str, Any], project: ProjectDefinition) -> str:
    member = route.get("member")
    if not isinstance(member, dict):
        raise ValidationError("deterministic routing target is missing its route member")
    if "builtin" in member:
        return str(member["builtin"])
    if "group" in member:
        return str(member["group"])
    if "auto_pool" in member:
        pool_id = str(member["auto_pool"])
        pool = next(
            (row for row in project.policies["pools"] if str(row["id"]) == pool_id),
            None,
        )
        if not isinstance(pool, dict):
            raise ValidationError(f"routing v2 audit cannot resolve pool {pool_id!r}")
        return str(pool["display_name"])
    raise ValidationError("deterministic routing target has an invalid route member")


def _audit_ai_materialization(
    project: ProjectDefinition,
    groups: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    policy = load_routing_policy_v2(project.policies)
    excluded = set(policy.ai.excluded_regions)
    ai_pools = [
        pool
        for pool in project.policies["pools"]
        if str(pool["source_use"]) == policy.scenario_use("ai")
    ]
    for pool in ai_pools:
        if {str(region) for region in pool["regions"]} & excluded:
            raise ValidationError(
                f"routing v2 AI pool {pool['id']!r} materializes an excluded region"
            )

    excluded_display_names = {
        display_name
        for region in excluded
        for display_name in _REGION_DISPLAY_NAMES.get(region, (f"AI · {region}",))
    }
    if excluded_display_names & set(groups):
        raise ValidationError("routing v2 generated an excluded AI region group")

    present_targets = {
        service for service, target in _AI_SERVICE_TARGETS.items() if target in groups
    }
    post_qualification = bool(present_targets)
    if post_qualification and present_targets != set(_AI_SERVICE_TARGETS):
        raise ValidationError("routing v2 candidate contains an incomplete AI service target set")

    materialized_anchors = 0
    for service, target in _AI_SERVICE_TARGETS.items():
        group = groups.get(target)
        if group is None:
            continue
        references = group.get("proxies")
        if not isinstance(references, list) or not references:
            raise ValidationError(f"AI service target {service!r} has no runtime references")
        prefix = _AI_SERVICE_PREFIXES[service]
        if references == ["REJECT"]:
            continue
        for reference in references:
            if not isinstance(reference, str) or not reference.startswith(prefix):
                raise ValidationError(
                    f"AI service target {service!r} references a non-service-qualified anchor"
                )
            anchor = groups.get(reference)
            if not isinstance(anchor, dict) or anchor.get("hidden") is not True:
                raise ValidationError(
                    f"AI service target {service!r} references a missing/non-hidden anchor"
                )
            materialized_anchors += 1

    return {
        "stage": "post_qualification" if post_qualification else "pre_qualification",
        "excluded_regions": sorted(excluded),
        "preferred_regions": list(policy.ai.preferred_regions),
        "ai_pools": len(ai_pools),
        "service_targets_checked": len(present_targets),
        "materialized_service_regions": materialized_anchors,
    }


def _audit_cutover_routes(
    policy: RoutingPolicyV2,
    groups: dict[str, dict[str, Any]],
    bindings: list[dict[str, Any]],
) -> dict[str, Any]:
    media_auto = groups.get("媒体自动")
    download_auto = groups.get("下载自动")
    if not isinstance(media_auto, dict) or media_auto.get("hidden") is not True:
        raise ValidationError("Routing V2 media scheduler must be hidden")
    if media_auto.get("type") != "url-test" or not media_auto.get("use"):
        raise ValidationError("Routing V2 media scheduler must be provider-backed url-test")
    if not isinstance(download_auto, dict) or download_auto.get("hidden") is not True:
        raise ValidationError("Routing V2 download scheduler must be hidden")
    if download_auto.get("type") != "url-test" or not download_auto.get("use"):
        raise ValidationError("Routing V2 download scheduler must be provider-backed url-test")

    if _group_proxies(groups, "油管视频") != ["媒体自动"]:
        raise ValidationError("YouTube must route only through the media scheduler")
    if _group_proxies(groups, "国外媒体") != ["媒体自动"]:
        raise ValidationError("foreign media must route only through the media scheduler")
    if _group_proxies(groups, "奈飞视频") != ["奈飞节点", "媒体自动"]:
        raise ValidationError("Netflix must use capability-first media fallback")

    if policy.download.mode == "general_auto":
        if _group_proxies(groups, "下载流量") != ["下载自动"]:
            raise ValidationError("download cutover must route only through download scheduler")
    elif _group_proxies(groups, "下载流量") != ["DIRECT"]:
        raise ValidationError("direct download mode must route only to DIRECT")

    by_source = {str(row["source_id"]): row for row in bindings}
    download = by_source.get("download")
    foreign_web = by_source.get("proxy_gfwlist")
    china_domain = by_source.get("china_domain")
    china_ip = by_source.get("china_company_ip")
    geoip = by_source.get("geoip_cn")
    if not all(isinstance(row, dict) for row in (download, foreign_web, china_domain, china_ip, geoip)):
        raise ValidationError("Routing V2 cutover classification bindings are incomplete")
    if download["target"] != "下载流量":
        raise ValidationError("Download.list must target the download scenario route")
    if not (
        int(china_domain["priority"])
        < int(download["priority"])
        < int(foreign_web["priority"])
        and int(china_ip["priority"]) < int(download["priority"])
        and int(geoip["priority"]) < int(download["priority"])
    ):
        raise ValidationError(
            "domestic classification must precede download and download must precede generic web"
        )

    ai_policy = _group_proxies(groups, "人工智能")
    preferred_names = [
        _REGION_CANONICAL_DISPLAY[region]
        for region in policy.ai.preferred_regions
        if region in _REGION_CANONICAL_DISPLAY
    ]
    ai_regions = [name for name in ai_policy if name != "DIRECT"]
    positions = [preferred_names.index(name) for name in ai_regions if name in preferred_names]
    if len(positions) != len(ai_regions) or positions != sorted(positions):
        raise ValidationError("generic AI country order does not follow Routing V2 preference")

    return {
        "media_scheduler": "媒体自动",
        "download_scheduler": "下载自动",
        "download_mode": policy.download.mode,
        "ai_region_order": ai_regions,
    }


def audit_routing_v2(
    project: ProjectDefinition,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Verify declared scenario contracts against the concrete runtime graph."""

    if project.acl4ssr is None:
        return {"status": "disabled"}
    model = compile_routing_model(project.acl4ssr)
    if model is None:
        return {"status": "disabled"}
    policy = load_routing_policy_v2(project.policies)
    groups = _groups(candidate)

    bindings_checked = 0
    for binding in model["bindings"]:
        scenario = str(binding["scenario"])
        expected_use = policy.scenario_use(scenario)
        actual_use = str(binding.get("source_use", "general"))
        if actual_use != expected_use:
            raise ValidationError(
                "routing v2 source-use contract mismatch: "
                f"{binding['source_id']!r} declares scenario {scenario!r} with use "
                f"{actual_use!r}, expected {expected_use!r}"
            )
        bindings_checked += 1

    deterministic_checked = 0
    for spec in project.acl4ssr.get("groups", []):
        route = spec.get("route")
        if not isinstance(route, dict) or route.get("deterministic") is not True:
            continue
        name = str(spec["display_name"])
        runtime = groups.get(name)
        if not isinstance(runtime, dict):
            raise ValidationError(f"routing v2 deterministic target {name!r} is missing")
        expected = _expected_member(route, project)
        references = runtime.get("proxies")
        if (
            runtime.get("hidden") is not True
            or runtime.get("type") != "select"
            or references != [expected]
        ):
            raise ValidationError(
                f"routing v2 deterministic target {name!r} is not a hidden one-hop route"
            )
        deterministic_checked += 1

    cutover = _audit_cutover_routes(policy, groups, model["bindings"])
    ai = _audit_ai_materialization(project, groups)
    visible = {
        str(row["name"])
        for row in candidate.get("proxy-groups", [])
        if isinstance(row, dict) and not row.get("hidden", False)
    }
    canonical_visible = {"代理选择", "网页浏览", "人工智能"}
    if canonical_visible <= set(groups):
        ai_wrapper_names = {
            str(pool["display_name"])
            for pool in project.policies["pools"]
            if str(pool["source_use"]) == policy.scenario_use("ai")
        }
        if ai["stage"] == "post_qualification":
            allowed_visible = canonical_visible
        else:
            allowed_visible = canonical_visible | ai_wrapper_names
        if not canonical_visible <= visible or not visible <= allowed_visible:
            raise ValidationError(
                "canonical routing v2 profile exposes unexpected top-level groups"
            )

    return {
        "status": "passed",
        "model_version": 2,
        "bindings_checked": bindings_checked,
        "deterministic_targets_checked": deterministic_checked,
        "visible_groups": len(visible),
        "scenario_counts": model["scenario_counts"],
        "policy": routing_policy_summary(policy),
        "cutover": cutover,
        "ai": ai,
    }
