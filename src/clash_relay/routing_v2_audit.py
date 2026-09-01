"""Fail-closed Routing Model V2 audits over the generated Mihomo graph."""

from __future__ import annotations

from typing import Any

from .config_loader import ProjectDefinition
from .errors import ValidationError
from .policy_contract import RuntimePolicyContract, load_policy_contract
from .routing_model import compile_routing_model
from .routing_policy_v2 import RoutingPolicyV2, load_routing_policy_v2, routing_policy_summary
from .runtime_graph import RuntimeGraph


def _groups(candidate: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return RuntimeGraph.from_candidate(candidate).groups


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
    contract: RuntimePolicyContract,
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
        for display_name in contract.ai.region_display_names.get(region, (f"AI · {region}",))
    }
    if excluded_display_names & set(groups):
        raise ValidationError("routing v2 generated an excluded AI region group")

    present_targets = {
        service
        for service, target in contract.ai.service_targets.items()
        if target in groups
    }
    post_qualification = bool(present_targets)
    if post_qualification and present_targets != set(contract.ai.service_targets):
        raise ValidationError("routing v2 candidate contains an incomplete AI service target set")

    materialized_anchors = 0
    for service, target in contract.ai.service_targets.items():
        group = groups.get(target)
        if group is None:
            continue
        references = group.get("proxies")
        if not isinstance(references, list) or not references:
            raise ValidationError(f"AI service target {service!r} has no runtime references")
        prefix = contract.ai.service_prefixes.get(service)
        if not prefix:
            raise ValidationError(f"routing contract has no AI service prefix for {service!r}")
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


def _audit_general_scheduler(groups: dict[str, dict[str, Any]], *, name: str, purpose: str) -> None:
    scheduler = groups.get(name)
    if not isinstance(scheduler, dict) or scheduler.get("hidden") is not True:
        raise ValidationError(f"Routing V2 {purpose} scheduler must be hidden")
    if scheduler.get("type") != "url-test" or not scheduler.get("use"):
        raise ValidationError(f"Routing V2 {purpose} scheduler must be provider-backed url-test")


def _audit_public_general_selector(
    groups: dict[str, dict[str, Any]],
    *,
    name: str,
    automatic: str,
    general_region_choices: tuple[str, ...],
) -> None:
    selector = groups.get(name)
    if not isinstance(selector, dict):
        raise ValidationError(f"Routing V2 public selector {name!r} is missing")
    if selector.get("hidden", False) or selector.get("type") != "select":
        raise ValidationError(f"Routing V2 public selector {name!r} must be a visible select group")
    if selector.get("use") or "filter" in selector:
        raise ValidationError(
            f"Routing V2 public selector {name!r} must not attach proxy providers directly"
        )
    expected = [automatic, *general_region_choices]
    if _group_proxies(groups, name) != expected:
        raise ValidationError(
            f"Routing V2 public selector {name!r} has unexpected general-only choices"
        )


def _audit_acl_compatibility_selectors(
    groups: dict[str, dict[str, Any]],
    contract: RuntimePolicyContract,
) -> None:
    for disabled in contract.disabled_groups:
        if disabled in groups:
            raise ValidationError(
                f"routing contract requires group {disabled!r} to remain disabled"
            )
    for name, expected in contract.compatibility_selectors.items():
        group = groups.get(name)
        if not isinstance(group, dict):
            raise ValidationError(f"ACL4SSR compatibility selector {name!r} is missing")
        if group.get("hidden") is not True or group.get("type") != "select":
            raise ValidationError(
                f"ACL4SSR compatibility selector {name!r} must remain a hidden select group"
            )
        if group.get("use") or "filter" in group:
            raise ValidationError(
                f"ACL4SSR compatibility selector {name!r} must not attach providers directly"
            )
        if _group_proxies(groups, name) != list(expected):
            raise ValidationError(
                f"ACL4SSR compatibility selector {name!r} changed its reference member order"
            )


def _audit_cutover_routes(
    policy: RoutingPolicyV2,
    groups: dict[str, dict[str, Any]],
    bindings: list[dict[str, Any]],
    contract: RuntimePolicyContract,
) -> dict[str, Any]:
    for purpose in ("media", "messaging", "download"):
        _audit_general_scheduler(
            groups,
            name=contract.automatic_groups[purpose],
            purpose=purpose,
        )
    for purpose in ("media", "messaging"):
        _audit_public_general_selector(
            groups,
            name=contract.public_groups[purpose],
            automatic=contract.automatic_groups[purpose],
            general_region_choices=contract.general_region_choices,
        )
    _audit_acl_compatibility_selectors(groups, contract)

    download_group = contract.public_groups["download"]
    if policy.download.mode == "general_auto":
        _audit_public_general_selector(
            groups,
            name=download_group,
            automatic=contract.automatic_groups["download"],
            general_region_choices=contract.general_region_choices,
        )
    elif _group_proxies(groups, download_group) != ["DIRECT"]:
        raise ValidationError("direct download mode must route only to DIRECT")

    by_source = {str(row["source_id"]): row for row in bindings}
    required_ids = set(contract.binding_targets)
    if not required_ids <= set(by_source):
        missing = ", ".join(sorted(required_ids - set(by_source)))
        raise ValidationError(f"ACL4SSR fidelity classification bindings are incomplete: {missing}")

    for source_id, expected_target in contract.binding_targets.items():
        if by_source[source_id]["target"] != expected_target:
            raise ValidationError(
                f"ACL4SSR fidelity binding {source_id!r} must target {expected_target!r}"
            )

    priorities = {source_id: int(by_source[source_id]["priority"]) for source_id in required_ids}
    for before, after in contract.priority_edges:
        if priorities[before] >= priorities[after]:
            raise ValidationError(
                f"ACL4SSR fidelity order requires {before!r} before {after!r}"
            )

    ai_policy = _group_proxies(groups, contract.public_groups["ai"])
    canonical_display = contract.ai.canonical_region_display
    preferred_names = [
        canonical_display[region]
        for region in policy.ai.preferred_regions
        if region in canonical_display
    ]
    ai_regions = [name for name in ai_policy if name != "DIRECT"]
    positions = [preferred_names.index(name) for name in ai_regions if name in preferred_names]
    if len(positions) != len(ai_regions) or positions != sorted(positions):
        raise ValidationError("generic AI country order does not follow Routing V2 preference")

    return {
        "media_scheduler": contract.automatic_groups["media"],
        "messaging_scheduler": contract.automatic_groups["messaging"],
        "download_scheduler": contract.automatic_groups["download"],
        "download_mode": policy.download.mode,
        "acl4ssr_baseline": contract.acl4ssr_baseline,
        "intentional_deviations": list(contract.intentional_deviations),
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
    contract = load_policy_contract(project.policies)
    graph = RuntimeGraph.from_candidate(candidate)
    groups = graph.groups

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

    cutover = _audit_cutover_routes(policy, groups, model["bindings"], contract)
    ai = _audit_ai_materialization(project, groups, contract)
    visible = {
        str(row["name"])
        for row in candidate.get("proxy-groups", [])
        if isinstance(row, dict) and not row.get("hidden", False)
    }
    canonical_visible = set(contract.visible_groups)
    if set(groups) >= canonical_visible:
        ai_wrapper_names = {
            str(pool["display_name"])
            for pool in project.policies["pools"]
            if str(pool["source_use"]) == policy.scenario_use("ai")
        }
        if ai["stage"] == "post_qualification":
            allowed_visible = canonical_visible
        else:
            allowed_visible = canonical_visible | ai_wrapper_names
        if not visible >= canonical_visible or not visible <= allowed_visible:
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
        "policy": routing_policy_summary(policy, project.policies),
        "cutover": cutover,
        "ai": ai,
    }
