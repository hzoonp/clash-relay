"""Deterministically construct a standalone Mihomo configuration."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .errors import GenerationError
from .models import Node
from .schema import load_and_validate
from .selector import select_nodes
from .util import safe_identifier, unique

_BUILTINS = {"DIRECT", "REJECT", "PASS", "COMPATIBLE"}


def _scope_token(value: str) -> str:
    return safe_identifier(value, upper=True, maximum=36)


def _runtime_name(node: Node, scope: str) -> str:
    digest = hashlib.sha256(f"{scope}\0{node.source_id}\0{node.fingerprint}".encode()).hexdigest()[
        :10
    ]
    original = node.original_name.replace("\n", " ").replace("\r", " ").strip()[:96]
    return f"[{scope}] {node.source_id}/{original} #{digest}"


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
        "expected-status": str(probe["expected_status"]),
    }


def _test_fields(probe: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": probe["url"],
        "interval": probe["interval"],
        "timeout": probe["timeout"],
        "lazy": probe["lazy"],
        "expected-status": str(probe["expected_status"]),
    }


def _normalized_selector(unit: dict[str, Any], *, service: bool) -> dict[str, Any]:
    if service:
        return {
            "source_use": unit["source_use"],
            "capabilities_any": [],
            "capabilities_all": list(unit["required_capabilities"]),
            "excluded_capabilities": list(unit["excluded_capabilities"]),
            "allowed_cost_levels": list(unit["allowed_cost_levels"]),
        }
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


def _add_fail_closed_group(groups: list[dict[str, Any]], fallback_name: str) -> None:
    groups.append(
        {
            "name": fallback_name,
            "type": "select",
            "hidden": True,
            "proxies": ["REJECT"],
        }
    )


def _add_public_group(
    groups: list[dict[str, Any]], *, display_name: str, fallback_name: str
) -> None:
    groups.append(
        {
            "name": display_name,
            "type": "select",
            "proxies": [fallback_name],
        }
    )


def _add_regular_unit(
    providers: dict[str, Any],
    groups: list[dict[str, Any]],
    nodes: list[Node],
    unit: dict[str, Any],
    *,
    probe: dict[str, Any],
    service: bool,
) -> dict[str, Any]:
    unit_id = unit["id"]
    token = _scope_token(unit_id)
    fallback_name = f"__CR_SERVICE_FALLBACK_{token}"
    regions = unit["countries"] if service else unit["regions"]
    fallback_order = unit["fallback_order"]
    selector = _normalized_selector(unit, service=service)
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
        _add_fail_closed_group(groups, fallback_name)
    else:
        fallback_group = {
            "name": fallback_name,
            "type": "fallback",
            "hidden": True,
            "proxies": fallback_proxies,
        }
        fallback_group.update(_test_fields(probe))
        groups.append(fallback_group)
    _add_public_group(groups, display_name=unit["display_name"], fallback_name=fallback_name)
    return {
        "id": unit_id,
        "display_name": unit["display_name"],
        "eligible_unique_nodes": len(total_fingerprints),
        "regions": region_counts,
        "fail_closed": not fallback_proxies,
    }


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
    fallback_name = f"__CR_SERVICE_FALLBACK_{token}"
    entry_nodes = _select_chain_leg(nodes, chain["entry"])
    exit_nodes = _select_chain_leg(nodes, chain["exit"])
    if not entry_nodes or not exit_nodes:
        if chain["on_empty"] == "error":
            missing = "entry" if not entry_nodes else "exit"
            raise GenerationError(f"chain {chain['id']!r} has no eligible {missing} nodes")
        _add_fail_closed_group(groups, fallback_name)
        _add_public_group(groups, display_name=chain["display_name"], fallback_name=fallback_name)
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
    fallback = {
        "name": fallback_name,
        "type": "fallback",
        "hidden": True,
        "proxies": [exit_auto],
    }
    fallback.update(_test_fields(probe))
    groups.append(fallback)
    _add_public_group(groups, display_name=chain["display_name"], fallback_name=fallback_name)
    return {
        "id": chain["id"],
        "display_name": chain["display_name"],
        "entry_nodes": len(entry_nodes),
        "exit_nodes": len(exit_nodes),
        "fail_closed": False,
    }


def _render_rule(rule: dict[str, Any], target: str) -> str:
    parts = [str(rule["type"]), str(rule["value"]), target]
    parts.extend(str(option) for option in rule.get("options", []))
    return ",".join(parts)


def _load_rules(root: Path, relative: str) -> list[dict[str, Any]]:
    document = load_and_validate(root / relative, "rules.schema.json")
    return list(document["rules"])


def _runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    runtime = config["runtime"]
    dns = runtime["dns"]
    return {
        "mixed-port": runtime["mixed_port"],
        "allow-lan": runtime["allow_lan"],
        "bind-address": runtime["bind_address"],
        "mode": runtime["mode"],
        "log-level": runtime["log_level"],
        "ipv6": runtime["ipv6"],
        "unified-delay": runtime["unified_delay"],
        "tcp-concurrent": runtime["tcp_concurrent"],
        "profile": {
            "store-selected": runtime["profile"]["store_selected"],
            "store-fake-ip": runtime["profile"]["store_fake_ip"],
        },
        "dns": {
            "enable": dns["enabled"],
            "enhanced-mode": dns["enhanced_mode"],
            "listen": dns["listen"],
            "nameserver": list(dns["nameservers"]),
            "fallback": list(dns["fallback_nameservers"]),
        },
    }


def generate_config(
    *,
    root: Path,
    config: dict[str, Any],
    services: dict[str, Any],
    policies: dict[str, Any],
    nodes: list[Node],
    external_rules: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    providers: dict[str, Any] = {}
    groups: list[dict[str, Any]] = []
    pool_report: list[dict[str, Any]] = []
    modules = config["modules"]

    pools = list(policies["pools"])
    general_pools = [pool for pool in pools if pool["id"] == "general"]
    other_pools = sorted(
        [pool for pool in pools if pool["id"] != "general"],
        key=lambda item: (item["rule_priority"], item["id"]),
    )
    ordered_units: list[tuple[str, dict[str, Any]]] = []
    ordered_units.extend(("pool", item) for item in general_pools)
    ordered_units.extend(
        ("service", item)
        for item in sorted(
            services["services"], key=lambda item: (item["rule_priority"], item["id"])
        )
    )
    ordered_units.extend(("pool", item) for item in other_pools)

    for kind, unit in ordered_units:
        if not modules.get(unit["module"], False):
            continue
        probe = unit["probe"] if kind == "service" else policies["probes"][unit["probe"]]
        pool_report.append(
            _add_regular_unit(
                providers,
                groups,
                nodes,
                unit,
                probe=probe,
                service=kind == "service",
            )
        )

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

    available_targets = _BUILTINS | {str(group["name"]) for group in groups}
    rule_rows: list[tuple[int, str, int, str]] = []
    for service in services["services"]:
        if modules.get(service["module"], False):
            for order, rule in enumerate(_load_rules(root, service["rules"])):
                rule_rows.append(
                    (
                        service["rule_priority"],
                        f"service:{service['id']}",
                        order,
                        _render_rule(rule, service["display_name"]),
                    )
                )
    for pool in policies["pools"]:
        if modules.get(pool["module"], False) and pool["rules"]:
            for order, rule in enumerate(_load_rules(root, pool["rules"])):
                rule_rows.append(
                    (
                        pool["rule_priority"],
                        f"pool:{pool['id']}",
                        order,
                        _render_rule(rule, pool["display_name"]),
                    )
                )
    for item in external_rules or []:
        target = str(item["target"])
        if target not in available_targets:
            raise GenerationError(
                f"external rule source {item['source_id']!r} targets unavailable group {target!r}"
            )
        rule_rows.append(
            (
                int(item["priority"]),
                f"acl4ssr:{item['source_id']}",
                int(item["order"]),
                _render_rule(item["rule"], target),
            )
        )

    rendered_rules = [
        _render_rule(rule, "DIRECT") for rule in _load_rules(root, "rules/direct.yaml")
    ]
    rendered_rules.extend(value for _, _, _, value in sorted(rule_rows, key=lambda item: item[:3]))
    final_target = "Proxy" if modules.get("general", False) else "DIRECT"
    rendered_rules.append(f"MATCH,{final_target}")
    rendered_rules = unique(rendered_rules)

    output = _runtime_config(config)
    output["proxy-providers"] = providers
    output["proxy-groups"] = groups
    output["rules"] = rendered_rules
    report = {
        "input_nodes": len(nodes),
        "providers": len(providers),
        "proxy_groups": len(groups),
        "public_groups": [group["name"] for group in groups if not group.get("hidden", False)],
        "routing_rules": len(rendered_rules),
        "pools": pool_report,
    }
    return output, report
