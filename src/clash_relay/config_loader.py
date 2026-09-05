"""Load public declarations and enforce cross-file semantics."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigurationError
from .models import SubscriptionSpec
from .policy_document import load_policy_document
from .schema import load_and_validate
from .status import parse_expected_status


@dataclass(frozen=True, slots=True)
class ProjectDefinition:
    root: Path
    config: dict[str, Any]
    subscriptions_document: dict[str, Any]
    subscriptions: tuple[SubscriptionSpec, ...]
    policies: dict[str, Any]
    acl4ssr: dict[str, Any] | None


def _ensure_unique(items: list[dict[str, Any]], field: str, label: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in items:
        value = str(item[field])
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        raise ConfigurationError(f"duplicate {label}: {', '.join(sorted(duplicates))}")


def _resolve_rule(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise ConfigurationError(f"rule path escapes the project root: {relative}") from exc
    if not target.is_file():
        raise ConfigurationError(f"rule file does not exist: {relative}")
    load_and_validate(target, "rules.schema.json")
    return target


def _resolve_acl4ssr_manifest(root: Path, relative: str) -> dict[str, Any]:
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise ConfigurationError(
            f"ACL4SSR manifest path escapes the project root: {relative}"
        ) from exc
    if not target.is_file():
        raise ConfigurationError(f"ACL4SSR manifest does not exist: {relative}")
    return load_and_validate(target, "acl4ssr.schema.json")


def _probe_semantics(probe: dict[str, Any], label: str) -> None:
    parse_expected_status(str(probe["expected_status"]))
    if probe["method"] != "HEAD":
        raise ConfigurationError(f"{label} must use HEAD because Mihomo provider checks use HEAD")


def _sniffer_semantics(config: dict[str, Any]) -> None:
    sniffer = config["runtime"].get("sniffer")
    if sniffer is None:
        return
    for protocol, settings in sniffer["sniff"].items():
        for port in settings["ports"]:
            if isinstance(port, int):
                continue
            start_text, end_text = str(port).split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if not 1 <= start <= end <= 65535:
                raise ConfigurationError(
                    f"runtime.sniffer.sniff.{protocol}.ports contains invalid range {port!r}"
                )


def _selector_capabilities(selector: dict[str, Any]) -> set[str]:
    return (
        set(selector["capabilities_any"])
        | set(selector["capabilities_all"])
        | set(selector["excluded_capabilities"])
    )


def _validate_acl4ssr_group_cycles(group_rows: list[dict[str, Any]]) -> None:
    names = {str(item["display_name"]) for item in group_rows}
    dependencies: dict[str, set[str]] = {}
    for item in group_rows:
        name = str(item["display_name"])
        dependencies[name] = {
            str(member["group"])
            for member in item["members"]
            if "group" in member and str(member["group"]) in names
        }

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visited:
            return
        if name in visiting:
            raise ConfigurationError(f"ACL4SSR routing groups contain a cycle at {name!r}")
        visiting.add(name)
        for dependency in dependencies[name]:
            visit(dependency)
        visiting.remove(name)
        visited.add(name)

    for name in sorted(names):
        visit(name)


def load_project(
    *,
    config_path: Path,
    subscriptions_path: Path,
    policies_path: Path,
) -> ProjectDefinition:
    config = load_and_validate(config_path, "config.schema.json")
    _sniffer_semantics(config)
    subscriptions_document = load_and_validate(subscriptions_path, "subscriptions.schema.json")
    policy_document = load_policy_document(policies_path)
    policies = policy_document.document
    root = Path(
        os.path.commonpath(
            [
                config_path.resolve().parent,
                subscriptions_path.resolve().parent,
                policies_path.resolve().parent,
            ]
        )
    )

    subscription_rows = subscriptions_document["subscriptions"]
    _ensure_unique(subscription_rows, "id", "subscription IDs")
    _ensure_unique(subscription_rows, "secret_name", "subscription secret names")
    pool_rows = policies["pools"]
    chain_rows = policies["chains"]
    _ensure_unique(pool_rows, "id", "pool IDs")
    _ensure_unique(chain_rows, "id", "chain IDs")
    all_units = pool_rows + chain_rows
    _ensure_unique(all_units, "id", "pool/chain IDs")
    _ensure_unique(all_units, "display_name", "public group names")

    modules = config["modules"]
    for item in all_units:
        if item["module"] not in modules:
            raise ConfigurationError(
                f"{item['id']!r} references undeclared module {item['module']!r}"
            )

    capability_definitions = policies["capabilities"]
    capabilities = set(capability_definitions)
    restricted = {key for key, value in capability_definitions.items() if bool(value["restricted"])}
    cost_levels = set(policies["cost_levels"])
    countries = set(policies["country_classification"]["aliases"])
    countries.add(policies["country_classification"]["default"])
    countries.update({"ANY", "OTHER", "*"})

    for country, patterns in policies["country_classification"]["aliases"].items():
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ConfigurationError(
                    f"country classifier {country!r} has invalid regex"
                ) from exc

    specs: list[SubscriptionSpec] = []
    for row in subscription_rows:
        unknown_caps = set(row["default_capabilities"]) - capabilities
        if unknown_caps:
            raise ConfigurationError(
                f"subscription {row['id']!r} uses unknown capabilities: {sorted(unknown_caps)}"
            )
        if row["default_cost_level"] not in cost_levels:
            raise ConfigurationError(f"subscription {row['id']!r} uses unknown cost level")
        unknown_countries = set(row["allowed_countries"]) - countries
        if unknown_countries:
            raise ConfigurationError(
                f"subscription {row['id']!r} uses unknown countries: {sorted(unknown_countries)}"
            )
        for pattern in row.get("deny_name_patterns", []):
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ConfigurationError(
                    f"subscription {row['id']!r} has an invalid deny-name regex"
                ) from exc
        for node_name, metadata in row.get("node_metadata", {}).items():
            node_caps = set(metadata.get("add_capabilities", [])) | set(
                metadata.get("remove_capabilities", [])
            )
            if node_caps - capabilities:
                raise ConfigurationError(f"node metadata {node_name!r} uses unknown capabilities")
            if metadata.get("cost_level", row["default_cost_level"]) not in cost_levels:
                raise ConfigurationError(f"node metadata {node_name!r} uses unknown cost level")
            if metadata.get("country", "OTHER") not in countries:
                raise ConfigurationError(f"node metadata {node_name!r} uses unknown country")
        for rule in row.get("name_rules", []):
            try:
                re.compile(rule["pattern"])
            except re.error as exc:
                raise ConfigurationError(
                    f"subscription {row['id']!r} has an invalid name rule regex"
                ) from exc
            inferred = set(rule.get("add_capabilities", []))
            if inferred - capabilities:
                raise ConfigurationError(
                    f"subscription {row['id']!r} name rule uses unknown capabilities"
                )
            if inferred & restricted and not rule.get("allow_restricted_capabilities", False):
                raise ConfigurationError(
                    f"subscription {row['id']!r} name rule attempts to infer restricted "
                    "capabilities without allow_restricted_capabilities: true"
                )
            if rule.get("cost_level", row["default_cost_level"]) not in cost_levels:
                raise ConfigurationError(
                    f"subscription {row['id']!r} name rule uses unknown cost level"
                )
        specs.append(
            SubscriptionSpec(
                id=row["id"],
                display_name=row["display_name"],
                enabled=row["enabled"],
                required=row["required"],
                secret_name=row["secret_name"],
                priority=row["ingest_order"],
                on_error=row["on_error"],
                allowed_uses=frozenset(row["allowed_uses"]),
                allowed_countries=frozenset(row["allowed_countries"]),
                default_capabilities=frozenset(row["default_capabilities"]),
                default_cost_level=row["default_cost_level"],
                max_node_multiplier=(
                    float(row["max_node_multiplier"])
                    if row.get("max_node_multiplier") is not None
                    else None
                ),
                deny_name_patterns=tuple(row.get("deny_name_patterns", [])),
                node_metadata=dict(row.get("node_metadata", {})),
                name_rules=tuple(row.get("name_rules", [])),
            )
        )

    probe_names = set(policies["probes"])
    for name, probe in policies["probes"].items():
        _probe_semantics(probe, f"probe {name!r}")
    for pool in pool_rows:
        if pool["probe"] not in probe_names:
            raise ConfigurationError(f"pool {pool['id']!r} references unknown probe")
        if _selector_capabilities(pool) - capabilities:
            raise ConfigurationError(f"pool {pool['id']!r} uses unknown capabilities")
        if set(pool["allowed_cost_levels"]) - cost_levels:
            raise ConfigurationError(f"pool {pool['id']!r} uses unknown cost levels")
        if set(pool["regions"]) - countries:
            raise ConfigurationError(f"pool {pool['id']!r} uses unknown regions")
        if not set(pool["fallback_order"]).issubset(pool["regions"]):
            raise ConfigurationError(f"pool {pool['id']!r} fallback_order is outside its regions")
        if pool["rules"]:
            _resolve_rule(root, pool["rules"])
    for chain in chain_rows:
        if chain["probe"] not in probe_names:
            raise ConfigurationError(f"chain {chain['id']!r} references unknown probe")
        for leg in (chain["entry"], chain["exit"]):
            if _selector_capabilities(leg) - capabilities:
                raise ConfigurationError(f"chain {chain['id']!r} uses unknown capabilities")
            if set(leg["allowed_cost_levels"]) - cost_levels:
                raise ConfigurationError(f"chain {chain['id']!r} uses unknown cost levels")
            if set(leg["countries"]) - countries:
                raise ConfigurationError(f"chain {chain['id']!r} uses unknown countries")

    direct = root / "rules" / "direct.yaml"
    if not direct.is_file():
        raise ConfigurationError("rules/direct.yaml is required")
    load_and_validate(direct, "rules.schema.json")

    acl4ssr: dict[str, Any] | None = None
    acl_config = config.get("rule_sources", {}).get("acl4ssr")
    if acl_config and acl_config["enabled"]:
        acl4ssr = _resolve_acl4ssr_manifest(root, str(acl_config["manifest"]))
        source_rows = list(acl4ssr["sources"])
        inline_rows = list(acl4ssr.get("inline_rules", []))
        group_rows = list(acl4ssr.get("groups", []))
        _ensure_unique(source_rows + inline_rows + group_rows, "id", "ACL4SSR declaration IDs")
        if group_rows:
            _ensure_unique(group_rows, "display_name", "ACL4SSR routing group names")
            existing_names = {str(item["display_name"]) for item in all_units}
            routing_names = {str(item["display_name"]) for item in group_rows}
            collisions = existing_names & routing_names
            if collisions:
                raise ConfigurationError(
                    "ACL4SSR routing group names collide with existing groups: "
                    + ", ".join(sorted(collisions))
                )
            pool_ids = {str(item["id"]) for item in pool_rows}
            known_names = existing_names | routing_names
            for group in group_rows:
                module = group.get("module")
                if module is not None and module not in modules:
                    raise ConfigurationError(
                        f"ACL4SSR routing group {group['id']!r} references undeclared module "
                        f"{module!r}"
                    )
                for member in group["members"]:
                    if "group" in member and str(member["group"]) not in known_names:
                        raise ConfigurationError(
                            f"ACL4SSR routing group {group['id']!r} references unknown group "
                            f"{member['group']!r}"
                        )
                    if "auto_pool" in member and str(member["auto_pool"]) not in pool_ids:
                        raise ConfigurationError(
                            f"ACL4SSR routing group {group['id']!r} references unknown auto pool "
                            f"{member['auto_pool']!r}"
                        )
            _validate_acl4ssr_group_cycles(group_rows)
        else:
            routing_names = set()

        declared_targets = {"DIRECT", "REJECT", "PASS", "COMPATIBLE"} | {
            str(item["display_name"]) for item in all_units
        }
        declared_targets |= routing_names
        for item in source_rows + inline_rows:
            module = item.get("module")
            if module is not None and module not in modules:
                raise ConfigurationError(
                    f"ACL4SSR source {item['id']!r} references undeclared module {module!r}"
                )
            if item["target"] not in declared_targets:
                raise ConfigurationError(
                    f"ACL4SSR source {item['id']!r} targets unknown group {item['target']!r}"
                )
        final_target = acl4ssr.get("final_target")
        if final_target is not None and str(final_target) not in declared_targets:
            raise ConfigurationError(
                f"ACL4SSR final_target references unknown group {final_target!r}"
            )
        for source in source_rows:
            relative = str(source["path"])
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts or not relative.startswith("Clash/"):
                raise ConfigurationError(
                    f"ACL4SSR source {source['id']!r} has an unsafe repository path"
                )

    return ProjectDefinition(
        root=root,
        config=config,
        subscriptions_document=subscriptions_document,
        subscriptions=tuple(specs),
        policies=policies,
        acl4ssr=acl4ssr,
    )
