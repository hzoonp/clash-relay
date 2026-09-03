"""Privacy-safe promotion guard against severe production inventory collapse."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config_loader import ProjectDefinition
from .errors import ConfigurationError, ValidationError
from .production_audit import audit_production_candidate
from .runtime_graph import RuntimeGraph
from .schema import load_and_validate


@dataclass(frozen=True, slots=True)
class PromotionGuardPolicy:
    enabled: bool
    minimum_total_node_ratio: float
    minimum_provider_ratio: float
    minimum_source_ratio_by_use: dict[str, float]
    minimum_sources_by_use: dict[str, int]


@dataclass(frozen=True, slots=True)
class InventoryCount:
    nodes: int
    providers: int
    sources_by_use: dict[str, int]
    providers_by_use: dict[str, int]
    nodes_by_use: dict[str, int]


def load_promotion_guard_policy(path: Path) -> PromotionGuardPolicy:
    document = load_and_validate(path, "promotion-guard.schema.json")
    if not isinstance(document, dict):
        raise ConfigurationError("promotion guard document must be a mapping")
    return PromotionGuardPolicy(
        enabled=bool(document["enabled"]),
        minimum_total_node_ratio=float(document["minimum_total_node_ratio"]),
        minimum_provider_ratio=float(document["minimum_provider_ratio"]),
        minimum_source_ratio_by_use={
            str(name): float(value)
            for name, value in document["minimum_source_ratio_by_use"].items()
        },
        minimum_sources_by_use={
            str(name): int(value) for name, value in document["minimum_sources_by_use"].items()
        },
    )


def _inventory(project: ProjectDefinition, candidate: dict[str, Any]) -> InventoryCount:
    graph = RuntimeGraph.from_candidate(candidate)
    audit = audit_production_candidate(project, candidate)

    runtime_nodes: set[str] = set()
    for names in graph.provider_proxies.values():
        runtime_nodes.update(names)

    sources: dict[str, set[str]] = {}
    providers_by_use: dict[str, int] = {}
    nodes_by_use: dict[str, int] = {}
    for row in audit.get("pools", []):
        if not isinstance(row, dict):
            continue
        source_use = str(row.get("source_use", "general"))
        raw_sources = row.get("sources", {})
        if not isinstance(raw_sources, dict):
            raise ValidationError("promotion guard received malformed production audit sources")
        sources.setdefault(source_use, set()).update(str(item) for item in raw_sources)
        providers_by_use[source_use] = providers_by_use.get(source_use, 0) + int(
            row.get("providers", 0) or 0
        )
        nodes_by_use[source_use] = nodes_by_use.get(source_use, 0) + int(row.get("nodes", 0) or 0)

    return InventoryCount(
        nodes=len(runtime_nodes),
        providers=len(graph.providers),
        sources_by_use={name: len(values) for name, values in sorted(sources.items())},
        providers_by_use=dict(sorted(providers_by_use.items())),
        nodes_by_use=dict(sorted(nodes_by_use.items())),
    )


def _ratio(current: int, baseline: int) -> float:
    if baseline <= 0:
        return 1.0
    return current / baseline


def _safe_inventory(value: InventoryCount) -> dict[str, Any]:
    uses = sorted(set(value.sources_by_use) | set(value.providers_by_use) | set(value.nodes_by_use))
    return {
        "nodes": value.nodes,
        "providers": value.providers,
        "uses": {
            use: {
                "sources": value.sources_by_use.get(use, 0),
                "providers": value.providers_by_use.get(use, 0),
                "node_entries": value.nodes_by_use.get(use, 0),
            }
            for use in uses
        },
    }


def assess_promotion(
    project: ProjectDefinition,
    candidate: dict[str, Any],
    baseline: dict[str, Any] | None,
    policy: PromotionGuardPolicy,
) -> dict[str, Any]:
    """Return an aggregate-only allow/block decision against current production."""

    if not policy.enabled:
        return {"status": "passed", "reason": "disabled", "violations": []}
    candidate_inventory = _inventory(project, candidate)
    if baseline is None:
        return {
            "status": "passed",
            "reason": "first_release",
            "candidate": _safe_inventory(candidate_inventory),
            "violations": [],
        }

    baseline_inventory = _inventory(project, baseline)
    violations: list[str] = []
    total_node_ratio = _ratio(candidate_inventory.nodes, baseline_inventory.nodes)
    provider_ratio = _ratio(candidate_inventory.providers, baseline_inventory.providers)
    if total_node_ratio < policy.minimum_total_node_ratio:
        violations.append("total_node_ratio")
    if provider_ratio < policy.minimum_provider_ratio:
        violations.append("provider_ratio")

    use_ratios: dict[str, dict[str, float | int]] = {}
    required_uses = set(policy.minimum_source_ratio_by_use) | set(policy.minimum_sources_by_use)
    for source_use in sorted(required_uses):
        current_sources = candidate_inventory.sources_by_use.get(source_use, 0)
        baseline_sources = baseline_inventory.sources_by_use.get(source_use, 0)
        source_ratio = _ratio(current_sources, baseline_sources)
        minimum_ratio = policy.minimum_source_ratio_by_use.get(source_use, 0.0)
        minimum_sources = policy.minimum_sources_by_use.get(source_use, 0)
        if current_sources < minimum_sources:
            violations.append(f"minimum_sources:{source_use}")
        if source_ratio < minimum_ratio:
            violations.append(f"source_ratio:{source_use}")
        use_ratios[source_use] = {
            "source_ratio": round(source_ratio, 4),
            "candidate_sources": current_sources,
            "baseline_sources": baseline_sources,
            "candidate_providers": candidate_inventory.providers_by_use.get(source_use, 0),
            "baseline_providers": baseline_inventory.providers_by_use.get(source_use, 0),
        }

    return {
        "status": "blocked" if violations else "passed",
        "reason": "degraded" if violations else "within_thresholds",
        "candidate": _safe_inventory(candidate_inventory),
        "baseline": _safe_inventory(baseline_inventory),
        "ratios": {
            "total_nodes": round(total_node_ratio, 4),
            "providers": round(provider_ratio, 4),
            "uses": use_ratios,
        },
        "thresholds": {
            "minimum_total_node_ratio": policy.minimum_total_node_ratio,
            "minimum_provider_ratio": policy.minimum_provider_ratio,
            "minimum_source_ratio_by_use": dict(sorted(policy.minimum_source_ratio_by_use.items())),
            "minimum_sources_by_use": dict(sorted(policy.minimum_sources_by_use.items())),
        },
        "violations": violations,
    }
