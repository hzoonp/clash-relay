"""Pure pool selection predicates."""

from __future__ import annotations

from typing import Any

from .models import Node


def _source_allows(node: Node, source_use: str) -> bool:
    return "*" in node.source_allowed_uses or source_use in node.source_allowed_uses


def _country_allowed_by_source(node: Node) -> bool:
    return "*" in node.source_allowed_countries or node.country in node.source_allowed_countries


def select_nodes(nodes: list[Node], selector: dict[str, Any], region: str) -> list[Node]:
    any_caps = set(selector.get("capabilities_any", []))
    all_caps = set(selector.get("capabilities_all", []))
    excluded = set(selector.get("excluded_capabilities", []))
    costs = set(selector.get("allowed_cost_levels", []))
    source_use = str(selector["source_use"])
    selected: list[Node] = []
    for node in nodes:
        if not _source_allows(node, source_use) or not _country_allowed_by_source(node):
            continue
        if region != "ANY" and node.country != region:
            continue
        if any_caps and not (any_caps & node.capabilities):
            continue
        if not all_caps.issubset(node.capabilities):
            continue
        if excluded & node.capabilities:
            continue
        if costs and node.cost_level not in costs:
            continue
        selected.append(node)
    # This order is deterministic only; source priority is never used as a quality score.
    return sorted(
        selected,
        key=lambda node: (
            node.source_priority,
            node.source_id,
            node.country,
            node.original_name.casefold(),
            node.fingerprint,
        ),
    )
