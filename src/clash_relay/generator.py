"""Deterministically construct a standalone Mihomo configuration."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .errors import GenerationError
from .models import Node
from .rule_compiler import RuleCompiler
from .runtime_config_renderer import RuntimeConfigRenderer
from .runtime_names import runtime_source_label, validate_runtime_source_labels
from .selector import select_nodes
from .util import normalize_expected_status, safe_identifier, unique


def _scope_token(value: str) -> str:
    return safe_identifier(value, upper=True, maximum=36)


def _runtime_name(node: Node, scope: str) -> str:
    digest = hashlib.sha256(f"{scope}\0{node.source_id}\0{node.fingerprint}".encode()).hexdigest()[
        :10
    ]
    original = node.original_name.replace("\n", " ").replace("\r", " ").strip()[:96]
    source_label = runtime_source_label(node.source_id)
    return f"[{scope}] {source_label}/{original} #{digest}"


def _runtime_proxy(node: Node, scope: str, *, dialer_proxy: str | None = None) -> dict[str, Any]:
    proxy = dict(node.proxy)
    proxy["name"] = _runtime_name(node, scope)
    if dialer_proxy is not None:
        proxy["dialer-proxy"] = dialer_proxy
    return proxy


def _health_check(probe: dict[str, Any]) -> dict[str, Any]:
    return {
        "enable": True,
        "url": probe["url"],
        "interval": probe["interval"],
        "timeout": probe["timeout"],
        "lazy": probe["lazy"],
        "expected-status": normalize_expected_status(probe["expected_status"]),
    }


def _test_fields(probe: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": probe["url"],
        "interval": probe["interval"],
        "timeout": probe["timeout"],
        "lazy": probe["lazy"],
        "expected-status": normalize_expected_status(probe["expected_status"]),
    }


def _normalized_selector(unit: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_use": unit["source_use"],
        "capabilities_any": list(unit["capabilities_any"]),
        "capabilities_all": list(unit["capabilities_all"]),
        "excluded_capabilities": list(unit["excluded_capabilities"]),
        "allowed_cost_levels": list(unit["allowed_cost_levels"]),
    }


def _internal_names(unit_id: str, region: str) -> tuple[str, str]:
    token = _scope_token(unit_id)
    region_token = _scope_token(region)
    return (
        f"cr_{safe_identifier(unit_id)}_{safe_identifier(region)}",
        f"__CR_AUTO_{token}_{region_token}",
    )


def _fallback_name(unit_id: str) -> str:
    return f"__CR_FALLBACK_{_scope_token(unit_id)}"


def _fail_closed_name(unit_id: str) -> str:
    return f"__CR_FAIL_CLOSED_{_scope_token(unit_id)}"


def _add_provider(
    providers: dict[str, Any],
    groups: list[dict[str, Any]],
    *,
    provider_name: str,
    auto_name: str,
    nodes: list[Node],
    scope: str,
    probe: dict[str, Any],
    dialer_proxy: str | None = None,
) -> None:
    payload = [_runtime_proxy(node, scope, dialer_proxy=dialer_proxy) for node in nodes]
    if not payload:
        raise GenerationError(f"refusing to generate empty provider {provider_name!r}")
    providers[provider_name] = {
        "type": "inline",
        "health-check": _health_check(probe),
        "payload": payload,
    }
    group = {
        "name": auto_name,
        "type": "url-test",
        "hidden": True,
        "use": [provider_name],
        "tolerance": probe["tolerance"],
    }
    group.update(_test_fields(probe))
    groups.append(group)


def _add_fail_closed_group(groups: list[dict[str, Any]], anchor_name: str) -> None:
    groups.append(
        {
            "name": anchor_name,
            "type": "select",
            "hidden": True,
            "proxies": ["REJECT"],
        }
    )


def _add_public_group(groups: list[dict[str, Any]], *, display_name: str, anchor_name: str) -> None:
    groups.append(
        {
            "name": display_name,
            "type": "select",
            "proxies": [anchor_name],
        }
    )


def _add_regular_pool(
    providers: dict[str, Any],
    groups: list[dict[str, Any]],
    nodes: list[Node],
    unit: dict[str, Any],
    *,
    probe: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    unit_id = unit["id"]
    token = _scope_token(unit_id)
    regions = unit["regions"]
    fallback_order = unit["fallback_order"]
    selector = _normalized_selector(unit)
    auto_by_region: dict[str, str] = {}
    region_counts: dict[str, int] = {}
    total_fingerprints: set[str] = set()
    for region in regions:
        selected = select_nodes(nodes, selector, region)
        region_counts[region] = len(selected)
        total_fingerprints.update(node.fingerprint for node in selected)
        if not selected:
            continue
        provider_name, auto_name = _internal_names(unit_id, region)
        _add_provider(
            providers,
            groups,
            provider_name=provider_name,
            auto_name=auto_name,
            nodes=selected,
            scope=f"{token}:{_scope_token(region)}",
            probe=probe,
        )
        auto_by_region[region] = auto_name

    fallback_proxies = [
        auto_by_region[region] for region in fallback_order if region in auto_by_region
    ]
    if not fallback_proxies:
        if unit["on_empty"] == "error":
            raise GenerationError(f"required pool {unit_id!r} has no eligible nodes")
        anchor_name = _fail_closed_name(unit_id)
        _add_fail_closed_group(groups, anchor_name)
    elif len(fallback_proxies) == 1:
        anchor_name = fallback_proxies[0]
    else:
        anchor_name = _fallback_name(unit_id)
        fallback_group = {
            "name": anchor_name,
            "type": "fallback",
            "hidden": True,
            "proxies": fallback_proxies,
        }
        fallback_group.update(_test_fields(probe))
        groups.append(fallback_group)

    _add_public_group(groups, display_name=unit["display_name"], anchor_name=anchor_name)
    return (
        {
            "id": unit_id,
            "display_name": unit["display_name"],
            "eligible_unique_nodes": len(total_fingerprints),
            "regions": region_counts,
            "fail_closed": not fallback_proxies,
        },
        anchor_name,
    )


def _select_chain_leg(nodes: list[Node], selector: dict[str, Any]) -> list[Node]:
    selected = select_nodes(nodes, selector, "ANY")
    countries = set(selector["countries"])
    if "ANY" in countries or "*" in countries:
        return selected
    return [node for node in selected if node.country in countries]


def _add_chain(
    providers: dict[str, Any],
    groups: list[dict[str, Any]],
    nodes: list[Node],
    chain: dict[str, Any],
    probe: dict[str, Any],
) -> dict[str, Any]:
    token = _scope_token(chain["id"])
    entry_auto = f"__CR_CHAIN_ENTRY_AUTO_{token}"
    exit_auto = f"__CR_CHAIN_EXIT_AUTO_{token}"
    entry_nodes = _select_chain_leg(nodes, chain["entry"])
    exit_nodes = _select_chain_leg(nodes, chain["exit"])
    if not entry_nodes or not exit_nodes:
        if chain["on_empty"] == "error":
            missing = "entry" if not entry_nodes else "exit"
            raise GenerationError(f"chain {chain['id']!r} has no eligible {missing} nodes")
        anchor_name = _fail_closed_name(chain["id"])
        _add_fail_closed_group(groups, anchor_name)
        _add_public_group(groups, display_name=chain["display_name"], anchor_name=anchor_name)
        return {
            "id": chain["id"],
            "display_name": chain["display_name"],
            "entry_nodes": len(entry_nodes),
            "exit_nodes": len(exit_nodes),
            "fail_closed": True,
        }

    entry_provider = f"cr_chain_entry_{safe_identifier(chain['id'])}"
    exit_provider = f"cr_chain_exit_{safe_identifier(chain['id'])}"
    _add_provider(
        providers,
        groups,
        provider_name=entry_provider,
        auto_name=entry_auto,
        nodes=entry_nodes,
        scope=f"CHAIN_ENTRY:{token}",
        probe=probe,
    )
    # The only place where dialer-proxy may enter generated output. Any such field
    # from the subscription was stripped before classification.
    _add_provider(
        providers,
        groups,
        provider_name=exit_provider,
        auto_name=exit_auto,
        nodes=exit_nodes,
        scope=f"CHAIN_EXIT:{token}",
        probe=probe,
        dialer_proxy=entry_auto,
    )
    _add_public_group(groups, display_name=chain["display_name"], anchor_name=exit_auto)
    return {
        "id": chain["id"],
        "display_name": chain["display_name"],
        "entry_nodes": len(entry_nodes),
        "exit_nodes": len(exit_nodes),
        "fail_closed": False,
    }


def _resolve_external_group_member(
    member: dict[str, Any],
    *,
    known_groups: set[str],
    auto_pools: dict[str, str],
) -> str | None:
    if "builtin" in member:
        return str(member["builtin"])
    if "group" in member:
        name = str(member["group"])
        return name if name in known_groups else None
    if "auto_pool" in member:
        name = auto_pools.get(str(member["auto_pool"]))
        return name if name in known_groups else None
    raise GenerationError("ACL4SSR routing group contains an invalid member")


def _add_external_groups(
    groups: list[dict[str, Any]],
    specs: list[dict[str, Any]],
    *,
    modules: dict[str, bool],
    auto_pools: dict[str, str],
) -> None:
    pending = [
        spec
        for spec in specs
        if spec.get("module") is None or modules.get(str(spec["module"]), False)
    ]
    known_groups = {str(group["name"]) for group in groups}

    while pending:
        unresolved: list[dict[str, Any]] = []
        progress = False
        for spec in pending:
            resolved_members: list[str] = []
            for member in spec["members"]:
                resolved = _resolve_external_group_member(
                    member,
                    known_groups=known_groups,
                    auto_pools=auto_pools,
                )
                if resolved is None:
                    break
                resolved_members.append(resolved)
            else:
                display_name = str(spec["display_name"])
                groups.append(
                    {
                        "name": display_name,
                        "type": "select",
                        "proxies": unique(resolved_members),
                    }
                )
                known_groups.add(display_name)
                progress = True
                continue
            unresolved.append(spec)

        if not progress:
            names = ", ".join(sorted(str(item["display_name"]) for item in unresolved))
            raise GenerationError(
                "ACL4SSR routing groups contain missing or cyclic references: " + names
            )
        pending = unresolved


def generate_config(
    *,
    root: Path,
    config: dict[str, Any],
    policies: dict[str, Any],
    nodes: list[Node],
    external_rule_providers: dict[str, Any] | None = None,
    external_rules: list[dict[str, Any]] | None = None,
    external_groups: list[dict[str, Any]] | None = None,
    final_target: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        validate_runtime_source_labels(node.source_id for node in nodes)
    except ValueError as exc:
        raise GenerationError(str(exc)) from exc

    providers: dict[str, Any] = {}
    groups: list[dict[str, Any]] = []
    pool_report: list[dict[str, Any]] = []
    pool_anchors: dict[str, str] = {}
    modules = config["modules"]

    pools = list(policies["pools"])
    general_pools = [pool for pool in pools if pool["id"] == "general"]
    other_pools = sorted(
        [pool for pool in pools if pool["id"] != "general"],
        key=lambda item: (item["rule_priority"], item["id"]),
    )
    for unit in [*general_pools, *other_pools]:
        if not modules.get(unit["module"], False):
            continue
        report, anchor_name = _add_regular_pool(
            providers,
            groups,
            nodes,
            unit,
            probe=policies["probes"][unit["probe"]],
        )
        pool_report.append(report)
        pool_anchors[str(unit["id"])] = anchor_name

    for chain in sorted(policies["chains"], key=lambda item: item["id"]):
        if not modules.get(chain["module"], False):
            continue
        pool_report.append(
            _add_chain(
                providers,
                groups,
                nodes,
                chain,
                policies["probes"][chain["probe"]],
            )
        )

    if not groups:
        raise GenerationError("no enabled module produced a public proxy group")

    _add_external_groups(
        groups,
        list(external_groups or []),
        modules=modules,
        auto_pools=pool_anchors,
    )

    rule_compilation = RuleCompiler(root).compile(
        modules=modules,
        policies=policies,
        groups=groups,
        external_rule_providers=external_rule_providers,
        external_rules=external_rules,
        final_target=final_target,
    )

    output = RuntimeConfigRenderer().render(config)
    output["proxy-providers"] = providers
    if rule_compilation.rule_providers:
        output["rule-providers"] = rule_compilation.rule_providers
    output["proxy-groups"] = groups
    output["rules"] = rule_compilation.rules
    report = {
        "input_nodes": len(nodes),
        "providers": len(providers),
        "rule_providers": len(rule_compilation.rule_providers),
        "proxy_groups": len(groups),
        "public_groups": [group["name"] for group in groups if not group.get("hidden", False)],
        "routing_rules": len(rule_compilation.rules),
        "pools": pool_report,
    }
    return output, report
