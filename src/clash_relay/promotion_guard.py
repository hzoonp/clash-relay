"""Privacy-safe promotion guard against production capability collapse."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .availability import (
    InventoryCount,
    ServiceAvailabilityCount,
    collect_inventory,
    collect_service_availability,
    ratio,
    safe_inventory,
    safe_service_availability,
)
from .config_loader import ProjectDefinition
from .errors import ConfigurationError
from .schema import load_and_validate
from .service_qualification import service_qualifications


@dataclass(frozen=True, slots=True)
class PromotionGuardPolicy:
    enabled: bool
    minimum_total_node_ratio: float
    minimum_provider_ratio: float
    minimum_source_ratio_by_use: dict[str, float]
    minimum_sources_by_use: dict[str, int]
    minimum_nodes_by_use: dict[str, int]
    minimum_regions_by_use: dict[str, int]
    minimum_qualified_nodes_by_service: dict[str, int]
    minimum_qualified_regions_by_service: dict[str, int]


def _float_map(value: dict[str, Any]) -> dict[str, float]:
    return {str(name): float(raw) for name, raw in value.items()}


def _int_map(value: dict[str, Any]) -> dict[str, int]:
    return {str(name): int(raw) for name, raw in value.items()}


def _validate_service_thresholds(name: str, values: dict[str, int]) -> None:
    supported = {service.label for service in service_qualifications()}
    unknown = set(values) - supported
    if unknown:
        rendered = ", ".join(sorted(unknown))
        raise ConfigurationError(
            f"promotion guard {name} references unknown services: {rendered}"
        )


def load_promotion_guard_policy(path: Path) -> PromotionGuardPolicy:
    document = load_and_validate(path, "promotion-guard.schema.json")
    if not isinstance(document, dict):
        raise ConfigurationError("promotion guard document must be a mapping")

    minimum_qualified_nodes_by_service = _int_map(
        document.get("minimum_qualified_nodes_by_service", {})
    )
    minimum_qualified_regions_by_service = _int_map(
        document.get("minimum_qualified_regions_by_service", {})
    )
    _validate_service_thresholds(
        "minimum_qualified_nodes_by_service", minimum_qualified_nodes_by_service
    )
    _validate_service_thresholds(
        "minimum_qualified_regions_by_service", minimum_qualified_regions_by_service
    )

    return PromotionGuardPolicy(
        enabled=bool(document["enabled"]),
        minimum_total_node_ratio=float(document["minimum_total_node_ratio"]),
        minimum_provider_ratio=float(document["minimum_provider_ratio"]),
        minimum_source_ratio_by_use=_float_map(document["minimum_source_ratio_by_use"]),
        minimum_sources_by_use=_int_map(document["minimum_sources_by_use"]),
        minimum_nodes_by_use=_int_map(document.get("minimum_nodes_by_use", {})),
        minimum_regions_by_use=_int_map(document.get("minimum_regions_by_use", {})),
        minimum_qualified_nodes_by_service=minimum_qualified_nodes_by_service,
        minimum_qualified_regions_by_service=minimum_qualified_regions_by_service,
    )


def _absolute_thresholds(policy: PromotionGuardPolicy) -> dict[str, Any]:
    return {
        "minimum_sources_by_use": dict(sorted(policy.minimum_sources_by_use.items())),
        "minimum_nodes_by_use": dict(sorted(policy.minimum_nodes_by_use.items())),
        "minimum_regions_by_use": dict(sorted(policy.minimum_regions_by_use.items())),
        "minimum_qualified_nodes_by_service": dict(
            sorted(policy.minimum_qualified_nodes_by_service.items())
        ),
        "minimum_qualified_regions_by_service": dict(
            sorted(policy.minimum_qualified_regions_by_service.items())
        ),
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


def _service_violations(
    availability: ServiceAvailabilityCount,
    policy: PromotionGuardPolicy,
) -> list[str]:
    violations: list[str] = []
    required_services = set(policy.minimum_qualified_nodes_by_service) | set(
        policy.minimum_qualified_regions_by_service
    )
    for service in sorted(required_services):
        qualified_nodes = availability.qualified_nodes_by_service.get(service, 0)
        qualified_regions = availability.qualified_regions_by_service.get(service, 0)
        minimum_nodes = policy.minimum_qualified_nodes_by_service.get(service, 0)
        minimum_regions = policy.minimum_qualified_regions_by_service.get(service, 0)
        if qualified_nodes < minimum_nodes:
            violations.append(f"minimum_qualified_nodes:{service}")
        if qualified_regions < minimum_regions:
            violations.append(f"minimum_qualified_regions:{service}")
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


def _candidate_inventory_summary(
    inventory: InventoryCount,
    service_availability: ServiceAvailabilityCount,
) -> dict[str, Any]:
    summary = safe_inventory(inventory)
    summary["services"] = safe_service_availability(service_availability)
    return summary


def assess_promotion(
    project: ProjectDefinition,
    candidate: dict[str, Any],
    baseline: dict[str, Any] | None,
    policy: PromotionGuardPolicy,
    *,
    qualification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an aggregate-only allow/block decision against availability requirements."""

    if not policy.enabled:
        return {"status": "passed", "reason": "disabled", "violations": []}

    candidate_inventory = collect_inventory(project, candidate)
    service_availability = collect_service_availability(qualification)
    violations = _absolute_violations(candidate_inventory, policy)
    violations.extend(_service_violations(service_availability, policy))
    candidate_summary = _candidate_inventory_summary(candidate_inventory, service_availability)
    if baseline is None:
        return {
            "status": "blocked" if violations else "passed",
            "reason": "availability_contract" if violations else "first_release",
            "candidate": candidate_summary,
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
        "candidate": candidate_summary,
        "baseline": safe_inventory(baseline_inventory),
        "ratios": {
            "total_nodes": round(total_node_ratio, 4),
            "providers": round(provider_ratio, 4),
            "uses": use_ratios,
        },
        "thresholds": {
            "minimum_total_node_ratio": policy.minimum_total_node_ratio,
            "minimum_provider_ratio": policy.minimum_provider_ratio,
            "minimum_source_ratio_by_use": dict(
                sorted(policy.minimum_source_ratio_by_use.items())
            ),
            **_absolute_thresholds(policy),
        },
        "violations": violations,
    }
