"""Aggregate production availability inventory shared by release gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config_loader import ProjectDefinition
from .production_audit import audit_production_candidate
from .runtime_graph import RuntimeGraph
from .util import safe_identifier


@dataclass(frozen=True, slots=True)
class InventoryCount:
    nodes: int
    providers: int
    sources_by_use: dict[str, int]
    providers_by_use: dict[str, int]
    nodes_by_use: dict[str, int]
    regions_by_use: dict[str, int]


def _provider_name(pool_id: str, region: str) -> str:
    return f"cr_{safe_identifier(pool_id)}_{safe_identifier(region)}"


def collect_inventory(project: ProjectDefinition, candidate: dict[str, Any]) -> InventoryCount:
    """Collect privacy-safe capability availability from one production candidate."""

    graph = RuntimeGraph.from_candidate(candidate)
    audit = audit_production_candidate(project, candidate)

    runtime_nodes: set[str] = set()
    for names in graph.provider_proxies.values():
        runtime_nodes.update(names)

    sources: dict[str, set[str]] = {}
    providers_by_use: dict[str, int] = {}
    nodes_by_use: dict[str, int] = {}
    regions: dict[str, set[str]] = {}
    pools = {str(pool["id"]): pool for pool in project.policies["pools"]}

    for row in audit.get("pools", []):
        if not isinstance(row, dict):
            continue
        source_use = str(row.get("source_use", "general"))
        raw_sources = row.get("sources", {})
        if not isinstance(raw_sources, dict):
            continue
        sources.setdefault(source_use, set()).update(str(item) for item in raw_sources)
        providers_by_use[source_use] = providers_by_use.get(source_use, 0) + int(
            row.get("providers", 0) or 0
        )
        nodes_by_use[source_use] = nodes_by_use.get(source_use, 0) + int(row.get("nodes", 0) or 0)
        regions.setdefault(source_use, set())

        pool = pools.get(str(row.get("id", "")))
        if pool is None:
            continue
        for region in pool["regions"]:
            provider_name = _provider_name(str(pool["id"]), str(region))
            if graph.provider_proxies.get(provider_name):
                regions[source_use].add(str(region))

    return InventoryCount(
        nodes=len(runtime_nodes),
        providers=len(graph.providers),
        sources_by_use={name: len(values) for name, values in sorted(sources.items())},
        providers_by_use=dict(sorted(providers_by_use.items())),
        nodes_by_use=dict(sorted(nodes_by_use.items())),
        regions_by_use={name: len(values) for name, values in sorted(regions.items())},
    )


def ratio(current: int, baseline: int) -> float:
    if baseline <= 0:
        return 1.0
    return current / baseline


def safe_inventory(value: InventoryCount) -> dict[str, Any]:
    """Render aggregate-only inventory suitable for logs and release proof."""

    uses = sorted(
        set(value.sources_by_use)
        | set(value.providers_by_use)
        | set(value.nodes_by_use)
        | set(value.regions_by_use)
    )
    return {
        "nodes": value.nodes,
        "providers": value.providers,
        "uses": {
            use: {
                "sources": value.sources_by_use.get(use, 0),
                "providers": value.providers_by_use.get(use, 0),
                "node_entries": value.nodes_by_use.get(use, 0),
                "regions": value.regions_by_use.get(use, 0),
            }
            for use in uses
        },
    }
