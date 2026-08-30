"""Deterministic capability and country classification."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .models import Node, SubscriptionSpec
from .util import stable_json


def proxy_fingerprint(proxy: dict[str, Any]) -> str:
    canonical = {key: value for key, value in proxy.items() if key != "name"}
    return hashlib.sha256(stable_json(canonical).encode("utf-8")).hexdigest()


def _name_rule_matches(pattern: str, name: str) -> bool:
    return re.search(pattern, name) is not None


def classify_proxy(
    proxy: dict[str, Any],
    spec: SubscriptionSpec,
    policies: dict[str, Any],
) -> Node:
    name = str(proxy["name"])
    country = policies["country_classification"]["default"]
    capabilities = set(spec.default_capabilities)
    cost_level = spec.default_cost_level

    # Name-based rules are auxiliary. They run first so exact per-node metadata wins.
    for rule in spec.name_rules:
        if not _name_rule_matches(rule["pattern"], name):
            continue
        if rule.get("country"):
            country = rule["country"]
        capabilities.update(rule.get("add_capabilities", []))
        capabilities.difference_update(rule.get("remove_capabilities", []))
        if rule.get("cost_level"):
            cost_level = rule["cost_level"]

    if country == policies["country_classification"]["default"]:
        for candidate, patterns in policies["country_classification"]["aliases"].items():
            if any(_name_rule_matches(pattern, name) for pattern in patterns):
                country = candidate
                break

    metadata = spec.node_metadata.get(name, {})
    if metadata.get("country"):
        country = metadata["country"]
    capabilities.update(metadata.get("add_capabilities", []))
    capabilities.difference_update(metadata.get("remove_capabilities", []))
    if metadata.get("cost_level"):
        cost_level = metadata["cost_level"]

    return Node(
        source_id=spec.id,
        source_display_name=spec.display_name,
        source_priority=spec.priority,
        source_allowed_uses=spec.allowed_uses,
        source_allowed_countries=spec.allowed_countries,
        original_name=name,
        proxy=dict(proxy),
        country=country,
        capabilities=frozenset(capabilities),
        cost_level=cost_level,
        fingerprint=proxy_fingerprint(proxy),
    )


def deduplicate_nodes(nodes: list[Node], policy: str) -> tuple[list[Node], int]:
    ordered = sorted(
        nodes,
        key=lambda node: (
            node.source_priority,
            node.source_id,
            node.original_name.casefold(),
            node.fingerprint,
        ),
    )
    seen: dict[str, Node] = {}
    result: list[Node] = []
    duplicates = 0
    for node in ordered:
        previous = seen.get(node.fingerprint)
        if previous is not None:
            duplicates += 1
            if policy == "error":
                from .errors import GenerationError

                raise GenerationError(
                    f"duplicate node fingerprint in sources {previous.source_id!r} and "
                    f"{node.source_id!r}"
                )
            continue
        seen[node.fingerprint] = node
        result.append(node)
    return result, duplicates
