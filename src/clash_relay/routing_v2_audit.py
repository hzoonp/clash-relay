"""Fail-closed Routing Model V2 audits over the generated Mihomo graph."""

from __future__ import annotations

from typing import Any

from .config_loader import ProjectDefinition
from .errors import ValidationError
from .routing_model import compile_routing_model
from .routing_policy_v2 import load_routing_policy_v2, routing_policy_summary

_BUILTINS = frozenset({"DIRECT", "REJECT", "PASS", "COMPATIBLE"})
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
        if set(str(region) for region in pool["regions"]) & excluded:
            raise ValidationError(
                f"routing v2 AI pool {pool['id']!r} materializes an excluded region"
            )

    for name in groups:
        if any(f"AI · {region}" == name for region in excluded):
            raise ValidationError("routing v2 generated an excluded AI region group")

    service_targets_checked = 0
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
            service_targets_checked += 1
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
        service_targets_checked += 1

    return {
        "excluded_regions": sorted(excluded),
        "preferred_regions": list(policy.ai.preferred_regions),
        "ai_pools": len(ai_pools),
        "service_targets_checked": service_targets_checked,
        "materialized_service_regions": materialized_anchors,
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

    visible = {
        str(row["name"])
        for row in candidate.get("proxy-groups", [])
        if isinstance(row, dict) and not row.get("hidden", False)
    }
    canonical_visible = {"代理选择", "网页浏览", "人工智能"}
    if canonical_visible <= set(groups) and visible != canonical_visible:
        raise ValidationError("canonical routing v2 profile exposes unexpected top-level groups")

    ai = _audit_ai_materialization(project, groups)
    return {
        "status": "passed",
        "model_version": 2,
        "bindings_checked": bindings_checked,
        "deterministic_targets_checked": deterministic_checked,
        "visible_groups": len(visible),
        "scenario_counts": model["scenario_counts"],
        "policy": routing_policy_summary(policy),
        "ai": ai,
    }
