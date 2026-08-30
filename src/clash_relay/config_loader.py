"""Load public declarations and enforce cross-file semantics."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigurationError
from .models import SubscriptionSpec
from .schema import load_and_validate
from .status import parse_expected_status


@dataclass(frozen=True, slots=True)
class ProjectDefinition:
    root: Path
    config: dict[str, Any]
    subscriptions_document: dict[str, Any]
    subscriptions: tuple[SubscriptionSpec, ...]
    services: dict[str, Any]
    policies: dict[str, Any]


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


def _probe_semantics(probe: dict[str, Any], label: str) -> None:
    parse_expected_status(str(probe["expected_status"]))
    if probe["method"] != "HEAD":
        raise ConfigurationError(f"{label} must use HEAD because Mihomo provider checks use HEAD")


def _selector_capabilities(selector: dict[str, Any]) -> set[str]:
    return set(selector["capabilities_any"]) | set(selector["capabilities_all"]) | set(
        selector["excluded_capabilities"]
    )


def load_project(
    *,
    config_path: Path,
    subscriptions_path: Path,
    services_path: Path,
    policies_path: Path,
) -> ProjectDefinition:
    config = load_and_validate(config_path, "config.schema.json")
    subscriptions_document = load_and_validate(
        subscriptions_path, "subscriptions.schema.json"
    )
    services = load_and_validate(services_path, "services.schema.json")
    policies = load_and_validate(policies_path, "policies.schema.json")
    root = Path(
        os.path.commonpath(
            [
                config_path.resolve().parent,
                subscriptions_path.resolve().parent,
                services_path.resolve().parent,
                policies_path.resolve().parent,
            ]
        )
    )

    subscription_rows = subscriptions_document["subscriptions"]
    _ensure_unique(subscription_rows, "id", "subscription IDs")
    _ensure_unique(subscription_rows, "secret_name", "subscription secret names")
    service_rows = services["services"]
    pool_rows = policies["pools"]
    chain_rows = policies["chains"]
    _ensure_unique(service_rows, "id", "service IDs")
    _ensure_unique(pool_rows, "id", "pool IDs")
    _ensure_unique(chain_rows, "id", "chain IDs")
    all_units = service_rows + pool_rows + chain_rows
    _ensure_unique(all_units, "id", "service/pool/chain IDs")
    _ensure_unique(all_units, "display_name", "public group names")

    modules = config["modules"]
    for item in all_units:
        if item["module"] not in modules:
            raise ConfigurationError(
                f"{item['id']!r} references undeclared module {item['module']!r}"
            )

    capability_definitions = policies["capabilities"]
    capabilities = set(capability_definitions)
    restricted = {
        key for key, value in capability_definitions.items() if bool(value["restricted"])
    }
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
            raise ConfigurationError(
                f"subscription {row['id']!r} uses unknown cost level"
            )
        unknown_countries = set(row["allowed_countries"]) - countries
        if unknown_countries:
            raise ConfigurationError(
                f"subscription {row['id']!r} uses unknown countries: {sorted(unknown_countries)}"
            )
        for node_name, metadata in row.get("node_metadata", {}).items():
            node_caps = set(metadata.get("add_capabilities", [])) | set(
                metadata.get("remove_capabilities", [])
            )
            if node_caps - capabilities:
                raise ConfigurationError(
                    f"node metadata {node_name!r} uses unknown capabilities"
                )
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
                priority=row["priority"],
                on_error=row["on_error"],
                allowed_uses=frozenset(row["allowed_uses"]),
                allowed_countries=frozenset(row["allowed_countries"]),
                default_capabilities=frozenset(row["default_capabilities"]),
                default_cost_level=row["default_cost_level"],
                node_metadata=dict(row.get("node_metadata", {})),
                name_rules=tuple(row.get("name_rules", [])),
            )
        )

    probe_names = set(policies["probes"])
    for name, probe in policies["probes"].items():
        _probe_semantics(probe, f"probe {name!r}")
    for service in service_rows:
        _probe_semantics(service["probe"], f"service {service['id']!r} probe")
        used_caps = set(service["required_capabilities"]) | set(
            service["excluded_capabilities"]
        )
        if used_caps - capabilities:
            raise ConfigurationError(f"service {service['id']!r} uses unknown capabilities")
        if set(service["allowed_cost_levels"]) - cost_levels:
            raise ConfigurationError(f"service {service['id']!r} uses unknown cost levels")
        if set(service["countries"]) - countries:
            raise ConfigurationError(f"service {service['id']!r} uses unknown countries")
        if not set(service["fallback_order"]).issubset(service["countries"]):
            raise ConfigurationError(
                f"service {service['id']!r} fallback_order is outside its countries"
            )
        _resolve_rule(root, service["rules"])
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
            raise ConfigurationError(
                f"pool {pool['id']!r} fallback_order is outside its regions"
            )
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
    return ProjectDefinition(
        root=root,
        config=config,
        subscriptions_document=subscriptions_document,
        subscriptions=tuple(specs),
        services=services,
        policies=policies,
    )
