"""Privacy-safe provider-neutral ServiceQualification result summaries."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .errors import ValidationError
from .service_qualification import ServiceQualification, service_qualifications

_MAX_OUTCOME_NAME = 64
_ALLOWED_OUTCOME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)


def _safe_count(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValidationError(f"ServiceQualification result {field} must be a non-negative integer")
    return value


def _safe_outcomes(value: Mapping[str, Any]) -> tuple[tuple[str, int], ...]:
    outcomes: list[tuple[str, int]] = []
    for raw_name, raw_count in sorted(value.items(), key=lambda item: str(item[0])):
        name = str(raw_name)
        if (
            not name
            or len(name) > _MAX_OUTCOME_NAME
            or any(character not in _ALLOWED_OUTCOME_CHARS for character in name)
        ):
            raise ValidationError("ServiceQualification outcome name is not aggregate-safe")
        outcomes.append((name, _safe_count(raw_count, f"outcome {name!r}")))
    return tuple(outcomes)


@dataclass(frozen=True, slots=True)
class ServiceQualificationResult:
    """One service's aggregate result with no node identities or raw responses."""

    service: str
    probe_name: str
    status: str
    tested_candidates: int
    qualified_candidates: int
    rejected_candidates: int
    live_tested_candidates: int
    live_qualified_candidates: int
    cache_pass_hits: int
    cache_fail_hits: int
    outcomes: tuple[tuple[str, int], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "service": self.service,
            "probe_name": self.probe_name,
            "status": self.status,
            "tested_candidates": self.tested_candidates,
            "qualified_candidates": self.qualified_candidates,
            "rejected_candidates": self.rejected_candidates,
            "live_tested_candidates": self.live_tested_candidates,
            "live_qualified_candidates": self.live_qualified_candidates,
            "cache_pass_hits": self.cache_pass_hits,
            "cache_fail_hits": self.cache_fail_hits,
            "outcomes": dict(self.outcomes),
        }


def build_service_qualification_result(
    service: ServiceQualification,
    *,
    live_tested: int,
    live_qualified: int,
    cache_pass_hits: int,
    cache_fail_hits: int,
    outcomes: Mapping[str, Any],
) -> ServiceQualificationResult:
    """Build the common aggregate shape used by every registered provider."""

    live_tested = _safe_count(live_tested, "live_tested")
    live_qualified = _safe_count(live_qualified, "live_qualified")
    cache_pass_hits = _safe_count(cache_pass_hits, "cache_pass_hits")
    cache_fail_hits = _safe_count(cache_fail_hits, "cache_fail_hits")
    if live_qualified > live_tested:
        raise ValidationError("ServiceQualification live qualified count exceeds live tested count")

    tested = live_tested + cache_pass_hits + cache_fail_hits
    qualified = live_qualified + cache_pass_hits
    rejected = tested - qualified
    return ServiceQualificationResult(
        service=service.label,
        probe_name=service.probe_name,
        status="qualified" if qualified else "rejected",
        tested_candidates=tested,
        qualified_candidates=qualified,
        rejected_candidates=rejected,
        live_tested_candidates=live_tested,
        live_qualified_candidates=live_qualified,
        cache_pass_hits=cache_pass_hits,
        cache_fail_hits=cache_fail_hits,
        outcomes=_safe_outcomes(outcomes),
    )


def service_qualification_results(ai_summary: Mapping[str, Any]) -> dict[str, dict[str, object]]:
    """Project the AI report into one safe result shape for every registered service."""

    diagnostics = ai_summary.get("diagnostics")
    probes = diagnostics.get("probes") if isinstance(diagnostics, dict) else None
    if not isinstance(probes, dict):
        raise ValidationError("ServiceQualification aggregate diagnostics require probe summaries")

    results: dict[str, dict[str, object]] = {}
    for service in service_qualifications():
        probe = probes.get(service.probe_name)
        if not isinstance(probe, dict):
            raise ValidationError(
                f"ServiceQualification aggregate diagnostics missing {service.probe_name!r}"
            )
        cache_pass_hits = _safe_count(probe.get("cache_pass_hits", 0), "cache_pass_hits")
        qualified = _safe_count(probe.get("qualified_nodes", 0), "qualified_nodes")
        if cache_pass_hits > qualified:
            raise ValidationError("ServiceQualification cached passes exceed qualified candidates")
        outcomes = probe.get("outcomes", {})
        if not isinstance(outcomes, dict):
            raise ValidationError("ServiceQualification outcomes must be an aggregate mapping")
        results[service.label] = build_service_qualification_result(
            service,
            live_tested=_safe_count(probe.get("live_tested_nodes", 0), "live_tested_nodes"),
            live_qualified=qualified - cache_pass_hits,
            cache_pass_hits=cache_pass_hits,
            cache_fail_hits=_safe_count(probe.get("cache_fail_hits", 0), "cache_fail_hits"),
            outcomes=outcomes,
        ).as_dict()
    return results
