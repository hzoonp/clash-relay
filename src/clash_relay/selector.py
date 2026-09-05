"""Pure pool selection predicates."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .models import Node, NodeOccurrence


def _occurrences(node: Node) -> tuple[NodeOccurrence, ...]:
    if node.occurrences:
        return node.occurrences
    return (
        NodeOccurrence(
            source_id=node.source_id,
            source_display_name=node.source_display_name,
            source_priority=node.source_priority,
            source_allowed_uses=node.source_allowed_uses,
            source_allowed_countries=node.source_allowed_countries,
            original_name=node.original_name,
            country=node.country,
            capabilities=node.capabilities,
            cost_level=node.cost_level,
        ),
    )


def _source_allows(item: NodeOccurrence, source_use: str) -> bool:
    return "*" in item.source_allowed_uses or source_use in item.source_allowed_uses


def _country_allowed_by_source(item: NodeOccurrence) -> bool:
    return "*" in item.source_allowed_countries or item.country in item.source_allowed_countries


def _eligible_occurrence(
    item: NodeOccurrence,
    *,
    source_use: str,
    region: str,
    any_caps: set[str],
    all_caps: set[str],
    excluded: set[str],
    costs: set[str],
) -> bool:
    if not _source_allows(item, source_use) or not _country_allowed_by_source(item):
        return False
    if region != "ANY" and item.country != region:
        return False
    if any_caps and not (any_caps & item.capabilities):
        return False
    if not all_caps.issubset(item.capabilities):
        return False
    if excluded & item.capabilities:
        return False
    return not (costs and item.cost_level not in costs)


def _project(node: Node, item: NodeOccurrence) -> Node:
    return replace(
        node,
        source_id=item.source_id,
        source_display_name=item.source_display_name,
        source_priority=item.source_priority,
        source_allowed_uses=item.source_allowed_uses,
        source_allowed_countries=item.source_allowed_countries,
        original_name=item.original_name,
        country=item.country,
        capabilities=item.capabilities,
        cost_level=item.cost_level,
    )


def select_nodes(nodes: list[Node], selector: dict[str, Any], region: str) -> list[Node]:
    any_caps = set(selector.get("capabilities_any", []))
    all_caps = set(selector.get("capabilities_all", []))
    excluded = set(selector.get("excluded_capabilities", []))
    costs = set(selector.get("allowed_cost_levels", []))
    source_use = str(selector["source_use"])
    selected: list[Node] = []
    for node in nodes:
        eligible = [
            item
            for item in _occurrences(node)
            if _eligible_occurrence(
                item,
                source_use=source_use,
                region=region,
                any_caps=any_caps,
                all_caps=all_caps,
                excluded=excluded,
                costs=costs,
            )
        ]
        if not eligible:
            continue
        chosen = min(
            eligible,
            key=lambda item: (
                item.source_priority,
                item.source_id,
                item.country,
                item.original_name.casefold(),
            ),
        )
        selected.append(_project(node, chosen))
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
