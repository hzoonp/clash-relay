"""Fail-closed production inventory drift guard.

The guard compares the already-published private config with the fully qualified
candidate immediately before publication.  It deliberately works from private
runtime bytes instead of persisting node identities or credentials.  Only
aggregate source and region counts leave this module.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

import yaml

from .errors import ValidationError

_SOURCE_NAME = re.compile(r"^\[[^\]]+\]\s+([a-z][a-z0-9_]*)/")
_BROWSING_PROVIDER = re.compile(r"^cr_browsing_([A-Z][A-Z0-9_]*)$")
_PROTECTED_REGIONS = ("US", "SG", "JP", "TW", "KR", "HK")
_MIN_PREVIOUS_SOURCE_NODES = 8
_MIN_PREVIOUS_REGION_NODES = 3
_MAX_SOURCE_DROP_RATIO = 0.60
_MAX_TOTAL_DROP_RATIO = 0.50


@dataclass(frozen=True, slots=True)
class InventorySnapshot:
    total_nodes: int
    sources: dict[str, int]
    regions: dict[str, dict[str, int]]


def _source_id(runtime_name: Any) -> str | None:
    if not isinstance(runtime_name, str):
        return None
    match = _SOURCE_NAME.match(runtime_name)
    return match.group(1) if match else None


def _proxy_fingerprint(proxy: dict[str, Any]) -> str:
    safe = {key: value for key, value in proxy.items() if key not in {"name", "dialer-proxy"}}
    payload = json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_config(content: bytes, label: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(content.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValidationError(f"{label} is not valid UTF-8 YAML") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be a YAML mapping")
    return value


def inventory_snapshot(config: dict[str, Any]) -> InventorySnapshot:
    providers = config.get("proxy-providers")
    if not isinstance(providers, dict):
        raise ValidationError("source health guard requires proxy-providers")

    source_nodes: dict[str, set[str]] = {}
    region_nodes: dict[str, dict[str, set[str]]] = {}
    all_nodes: set[str] = set()

    for provider_name, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        payload = provider.get("payload")
        if not isinstance(payload, list):
            continue
        region_match = _BROWSING_PROVIDER.match(str(provider_name))
        region = region_match.group(1) if region_match else None
        for proxy in payload:
            if not isinstance(proxy, dict):
                continue
            source = _source_id(proxy.get("name"))
            if source is None:
                continue
            fingerprint = _proxy_fingerprint(proxy)
            source_nodes.setdefault(source, set()).add(fingerprint)
            all_nodes.add(f"{source}:{fingerprint}")
            if region is not None:
                region_nodes.setdefault(source, {}).setdefault(region, set()).add(fingerprint)

    if not source_nodes:
        raise ValidationError("source health guard could not recover any source inventory")

    return InventorySnapshot(
        total_nodes=len(all_nodes),
        sources={source: len(nodes) for source, nodes in sorted(source_nodes.items())},
        regions={
            source: {region: len(nodes) for region, nodes in sorted(regions.items())}
            for source, regions in sorted(region_nodes.items())
        },
    )


def evaluate_source_health(
    previous: dict[str, Any],
    candidate: dict[str, Any],
    *,
    declared_sources: set[str],
    browsing_sources: set[str],
) -> dict[str, Any]:
    before = inventory_snapshot(previous)
    after = inventory_snapshot(candidate)
    violations: list[dict[str, Any]] = []

    previous_sources = set(before.sources)
    planned_removed_sources = previous_sources - declared_sources
    active_sources = sorted(previous_sources & declared_sources)

    if not planned_removed_sources and before.total_nodes >= _MIN_PREVIOUS_SOURCE_NODES:
        drop_ratio = 1.0 - (after.total_nodes / before.total_nodes)
        if drop_ratio > _MAX_TOTAL_DROP_RATIO:
            violations.append(
                {
                    "kind": "total_inventory_drop",
                    "before": before.total_nodes,
                    "after": after.total_nodes,
                    "drop_ratio": round(drop_ratio, 4),
                }
            )

    for source in active_sources:
        old_count = before.sources[source]
        new_count = after.sources.get(source, 0)
        if old_count >= _MIN_PREVIOUS_SOURCE_NODES:
            drop_ratio = 1.0 - (new_count / old_count)
            if drop_ratio > _MAX_SOURCE_DROP_RATIO:
                violations.append(
                    {
                        "kind": "source_inventory_drop",
                        "source": source,
                        "before": old_count,
                        "after": new_count,
                        "drop_ratio": round(drop_ratio, 4),
                    }
                )

        if source not in browsing_sources:
            continue
        old_regions = before.regions.get(source, {})
        new_regions = after.regions.get(source, {})
        for region in _PROTECTED_REGIONS:
            old_region_count = int(old_regions.get(region, 0))
            new_region_count = int(new_regions.get(region, 0))
            if old_region_count >= _MIN_PREVIOUS_REGION_NODES and new_region_count == 0:
                violations.append(
                    {
                        "kind": "protected_region_disappeared",
                        "source": source,
                        "region": region,
                        "before": old_region_count,
                        "after": 0,
                    }
                )

    return {
        "status": "rejected" if violations else "healthy",
        "previous_total_nodes": before.total_nodes,
        "candidate_total_nodes": after.total_nodes,
        "previous_sources": len(before.sources),
        "candidate_sources": len(after.sources),
        "planned_removed_sources": sorted(planned_removed_sources),
        "thresholds": {
            "minimum_previous_source_nodes": _MIN_PREVIOUS_SOURCE_NODES,
            "minimum_previous_region_nodes": _MIN_PREVIOUS_REGION_NODES,
            "maximum_source_drop_ratio": _MAX_SOURCE_DROP_RATIO,
            "maximum_total_drop_ratio": _MAX_TOTAL_DROP_RATIO,
            "protected_regions": list(_PROTECTED_REGIONS),
        },
        "violations": violations,
    }


def evaluate_source_health_bytes(
    previous: bytes,
    candidate: bytes,
    *,
    declared_sources: set[str],
    browsing_sources: set[str],
) -> dict[str, Any]:
    return evaluate_source_health(
        _load_config(previous, "published production config"),
        _load_config(candidate, "production candidate"),
        declared_sources=declared_sources,
        browsing_sources=browsing_sources,
    )
