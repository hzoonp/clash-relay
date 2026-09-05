"""Privacy-safe promotion guard against production capability collapse."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .availability import InventoryCount, collect_inventory, ratio, safe_inventory
from .config_loader import ProjectDefinition
from .errors import ConfigurationError
from .schema import load_and_validate


@dataclass(frozen=True, slots=True)
class PromotionGuardPolicy:
    enabled: bool
    minimum_total_node_ratio: float
    minimum_provider_ratio: float
    minimum_source_ratio_by_use: dict[str, float]
    minimum_sources_by_use: dict[str, int]
    minimum_nodes_by_use: dict[str, int]
    minimum_regions_by_use: dict[str, int]


def _float_map(value: dict[str, Any]) -> dict[str, float]:
    return {str(name): float(raw) for name, raw in value.items()}


def _int_map(value: dict[str, Any]) -> dict[str, int]:
    return {str(name): int(raw) for name, raw in value.items()}


def load_promotion_guard_policy(path: Path) -> PromotionGuardPolicy:
    document = load_and_validate(path, "promotion-guard.schema.json")
    if not isinstance(document, dict):
        raise ConfigurationError("promotion guard document must be a mapping")

    return PromotionGuardPolicy(
        enabled=bool(document["enabled"]),
        minimum_total_node_ratio=float(document["minimum_total_node_ratio"]),
        minimum_provider_ratio=float(document["minimum_provider_ratio"]),
        minimum_source_ratio_by_use=_float_map(document["minimum_source_ratio_by_use"]),
        minimum_sources_by_use=_int_map(document["minimum_sources_by_use"]),
        minimum_nodes_by_use=_int_map(document.get("minimum_nodes_by_use", {})),
        minimum_regions_by_use=_int_map(document.get("minimum_regions_by_use", {})),
    )


def _absolute_thresholds(policy: PromotionGuardPolicy) -> dict[str, Any]:
    return {
        "minimum_sources_by_use": dict(sorted(policy.minimum_sources_by_use.items())),
        "minimum_nodes_by_use": dict(sorted(policy.minimum_nodes_by_use.items())),
        "minimum_regions_by_use": dict(sorted(policy.minimum_regions_by_use.items())),
    }


def _absolute_violations(
    candidate_inventory: InventoryCount,
    policy: PromotionGuardPolicy,
) -> list[str]:
    violations: list[str] = []
    required_uses = (
        set(policy.minimum_sources_by_use)
        | set(policy.minimum_nodes_by_use)
        | set(policy.minimum_regions_by_use)
    )

    for source_use in sorted(required_uses):
        source_count = candidate_inventory.sources_by_use.get(source_use, 0)
        node_count = candidate_inventory.nodes_by_use.get(source_use, 0)
        region_count = candidate_inventory.regions_by_use.get(source_use, 0)
        minimum_sources = policy.minimum_sources_by_use.get(source_use, 0)
        minimum_nodes = policy.minimum_nodes_by_use.get(source_use, 0)
        minimum_regions = policy.minimum_regions_by_use.get(source_use, 0)

        if source_count < minimum_sources:
            violations.append(f"minimum_sources:{source_use}")
        if node_count < minimum_nodes:
            violations.append(f"minimum_nodes:{source_use}")
        if region_count < minimum_regions:
            violations.append(f"minimum_regions:{source_use}")

    return violations


def _use_ratio_row(
    source_use: str,
    candidate_inventory: InventoryCount,
    baseline_inventory: InventoryCount,
) -> dict[str, float | int | bool]:
    current_sources = candidate_inventory.sources_by_use.get(source_use, 0)
    baseline_sources = baseline_inventory.sources_by_use.get(source_use, 0)
    return {
        "configured_in_baseline": source_use in baseline_inventory.sources_by_use,
        "source_ratio": round(ratio(current_sources, baseline_sources), 4),
        "candidate_sources": current_sources,
        "baseline_sources": baseline_sources,
        "candidate_providers": candidate_inventory.providers_by_use.get(source_use, 0),
        "baseline_providers": baseline_inventory.providers_by_use.get(source_use, 0),
        "candidate_nodes": candidate_inventory.nodes_by_use.get(source_use, 0),
        "baseline_nodes": baseline_inventory.nodes_by_use.get(source_use, 0),
        "candidate_regions": candidate_inventory.regions_by_use.get(source_use, 0),
        "baseline_regions": baseline_inventory.regions_by_use.get(source_use, 0),
    }


def assess_promotion(
    project: ProjectDefinition,
    candidate: dict[str, Any],
    baseline: dict[str, Any] | None,
    policy: PromotionGuardPolicy,
) -> dict[str, Any]:
    """Return an aggregate-only allow/block decision against availability requirements."""

    if not policy.enabled:
        return {"status": "passed", "reason": "disabled", "violations": []}

    candidate_inventory = collect_inventory(project, candidate)
    violations = _absolute_violations(candidate_inventory, policy)
    if baseline is None:
        return {
            "status": "blocked" if violations else "passed",
            "reason": "availability_contract" if violations else "first_release",
            "candidate": safe_inventory(candidate_inventory),
            "thresholds": _absolute_thresholds(policy),
            "violations": violations,
        }

    baseline_inventory = collect_inventory(project, baseline)
    total_node_ratio = ratio(candidate_inventory.nodes, baseline_inventory.nodes)
    provider_ratio = ratio(candidate_inventory.providers, baseline_inventory.providers)
    if total_node_ratio < policy.minimum_total_node_ratio:
        violations.append("total_node_ratio")
    if provider_ratio < policy.minimum_provider_ratio:
        violations.append("provider_ratio")

    use_ratios: dict[str, dict[str, float | int | bool]] = {}
    for source_use in sorted(policy.minimum_source_ratio_by_use):
        row = _use_ratio_row(source_use, candidate_inventory, baseline_inventory)
        configured = bool(row["configured_in_baseline"])
        source_ratio = float(row["source_ratio"])
        minimum_ratio = policy.minimum_source_ratio_by_use[source_use]
        if configured and source_ratio < minimum_ratio:
            violations.append(f"source_ratio:{source_use}")
        use_ratios[source_use] = row

    return {
        "status": "blocked" if violations else "passed",
        "reason": "degraded" if violations else "within_thresholds",
        "candidate": safe_inventory(candidate_inventory),
        "baseline": safe_inventory(baseline_inventory),
        "ratios": {
            "total_nodes": round(total_node_ratio, 4),
            "providers": round(provider_ratio, 4),
            "uses": use_ratios,
        },
        "thresholds": {
            "minimum_total_node_ratio": policy.minimum_total_node_ratio,
            "minimum_provider_ratio": policy.minimum_provider_ratio,
            "minimum_source_ratio_by_use": dict(sorted(policy.minimum_source_ratio_by_use.items())),
            **_absolute_thresholds(policy),
        },
        "violations": violations,
    }
