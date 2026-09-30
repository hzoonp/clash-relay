"""Shared, source-neutral building blocks for regional web scheduling."""

from __future__ import annotations

from typing import Any

from .runtime_graph import RuntimeGraph
from .runtime_names import parse_runtime_source_name
from .util import stable_json


def proxy_identity(proxy: dict[str, Any]) -> str:
    """Only alias the same source and complete transport/dialer configuration."""
    name = str(proxy.get("name", ""))
    return stable_json(
        {
            "source": parse_runtime_source_name(name) or name,
            "proxy": {key: value for key, value in proxy.items() if key != "name"},
        }
    )


def regional_groups(
    *,
    provider_names: list[str],
    stable_name: str,
    reserve_name: str,
    region_name: str,
    node_filter: str,
    scheduler_fields: dict[str, Any],
    region_fields: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        *[
            {
                "name": name,
                "type": "url-test",
                "hidden": True,
                "use": list(provider_names),
                "filter": node_filter,
                **scheduler_fields,
            }
            for name in (stable_name, reserve_name)
        ],
        {
            "name": region_name,
            "type": "fallback",
            "hidden": True,
            "proxies": [stable_name, reserve_name],
            **region_fields,
        },
    ]


def drop_unused_pool_scaffold(config: dict[str, Any], *, pool: str, inventory: str) -> int:
    """Remove replaced generator schedulers, preserving explicit fork references."""
    graph = RuntimeGraph.from_candidate(config)
    candidates = {
        name
        for name in graph.groups
        if name.startswith(f"__CR_AUTO_{pool}_") or name in {f"__CR_FALLBACK_{pool}", inventory}
    }
    external_references = {
        reference
        for name in graph.groups.keys() - candidates
        for reference in graph.group_members(name)
        if reference in candidates
    }
    retained = graph.reachable_groups(external_references) & candidates
    removed = candidates - retained
    config["proxy-groups"][:] = [
        group for group in config["proxy-groups"] if group.get("name") not in removed
    ]
    return len(removed)
