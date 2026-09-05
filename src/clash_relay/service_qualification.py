"""Generic AI service qualification extension points.

The production qualification pipeline depends on this registry rather than on
provider-specific modules. Provider implementations own probe expansion, cache
policy, optional route post-processing, diagnostics, and client-path hardening.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .policy_document import load_policy_document
from .scheduler_policy import AICachePolicy


@dataclass(frozen=True, slots=True)
class ServiceQualification:
    """One independently extensible service qualification contract."""

    probe_name: str
    label: str
    target_group: str

    def cache_key(self) -> str:
        return self.probe_name

    def cache_ttls(self, policy: AICachePolicy) -> tuple[int, int]:
        return policy.pass_ttl_seconds, policy.failure_ttl_seconds

    def qualification_probes(self, primary: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        return (primary,)

    def supporting_probes(self) -> tuple[dict[str, Any], ...]:
        return ()

    def diagnostics_key(self) -> str | None:
        return None

    def build_extended_diagnostics(
        self,
        *,
        live_tested: int,
        live_qualified: set[str],
        qualification_diagnostics: dict[str, Any],
        supporting_diagnostics: dict[str, Any],
        supporting_qualified: set[str],
    ) -> dict[str, Any] | None:
        del live_tested, live_qualified, qualification_diagnostics
        del supporting_diagnostics, supporting_qualified
        return None

    def cache_metadata(self) -> dict[str, object]:
        return {}

    def route_postprocess(self, candidate: Path) -> tuple[str, dict[str, Any]] | None:
        del candidate
        return None

    @property
    def supports_client_path_hardening(self) -> bool:
        return False

    def harden_client_path(self, candidate: Path) -> dict[str, Any]:
        del candidate
        raise ValidationError(f"service {self.probe_name!r} does not support client-path hardening")


def _network_failure_count(probes: dict[str, Any], outcome: str) -> int:
    total = 0
    for summary in probes.values():
        if not isinstance(summary, dict):
            continue
        outcomes = summary.get("outcomes")
        if isinstance(outcomes, dict):
            total += int(outcomes.get(outcome, 0))
    return total


@dataclass(frozen=True, slots=True)
class OpenAIQualification(ServiceQualification):
    probe_name: str = "ai_openai"
    label: str = "openai"
    target_group: str = "__CR_AI_SERVICE_OPENAI"

    def cache_key(self) -> str:
        from .openai_app_contract import cache_service_key

        return cache_service_key(self.probe_name)

    def cache_ttls(self, policy: AICachePolicy) -> tuple[int, int]:
        return policy.openai_pass_ttl_seconds, policy.openai_failure_ttl_seconds

    def qualification_probes(self, primary: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        from .openai_app_contract import critical_probes

        return critical_probes(primary)

    def supporting_probes(self) -> tuple[dict[str, Any], ...]:
        from .openai_app_contract import supporting_probes

        return supporting_probes()

    def diagnostics_key(self) -> str:
        return "openai_app"

    def build_extended_diagnostics(
        self,
        *,
        live_tested: int,
        live_qualified: set[str],
        qualification_diagnostics: dict[str, Any],
        supporting_diagnostics: dict[str, Any],
        supporting_qualified: set[str],
    ) -> dict[str, Any]:
        from .openai_app_contract import contract_summary

        critical = qualification_diagnostics.get("probes", {})
        if not isinstance(critical, dict):
            critical = {}
        supporting = supporting_diagnostics.get("probes", {})
        if not isinstance(supporting, dict):
            supporting = {}
        return {
            "contract": contract_summary(),
            "critical": {
                "live_tested_nodes": live_tested,
                "app_ready_live_nodes": len(live_qualified),
                "endpoint_count": len(critical),
                "tls_errors": _network_failure_count(critical, "tls_error"),
                "dns_errors": _network_failure_count(critical, "dns_error"),
                "timeouts": _network_failure_count(critical, "timeout"),
                "probes": critical,
            },
            "supporting": {
                "live_tested_nodes": int(supporting_diagnostics.get("tested_nodes", 0)),
                "fully_reachable_nodes": len(supporting_qualified),
                "endpoint_count": len(supporting),
                "tls_errors": _network_failure_count(supporting, "tls_error"),
                "dns_errors": _network_failure_count(supporting, "dns_error"),
                "timeouts": _network_failure_count(supporting, "timeout"),
                "probes": supporting,
            },
        }

    def cache_metadata(self) -> dict[str, object]:
        from .openai_app_contract import contract_summary

        return {"openai_contract_fingerprint": contract_summary()["fingerprint"]}

    def route_postprocess(self, candidate: Path) -> tuple[str, dict[str, Any]]:
        from .openai_app_contract import rewrite_route_locked_candidate

        return "openai_app_route_lock", rewrite_route_locked_candidate(candidate)

    @property
    def supports_client_path_hardening(self) -> bool:
        return True

    def harden_client_path(self, candidate: Path) -> dict[str, Any]:
        from .openai_application import harden_openai_client_path

        return harden_openai_client_path(candidate)


@dataclass(frozen=True, slots=True)
class ClaudeQualification(ServiceQualification):
    probe_name: str = "ai_claude"
    label: str = "claude"
    target_group: str = "__CR_AI_SERVICE_CLAUDE"


@dataclass(frozen=True, slots=True)
class GeminiQualification(ServiceQualification):
    probe_name: str = "ai_gemini"
    label: str = "gemini"
    target_group: str = "__CR_AI_SERVICE_GEMINI"


_DEFAULT_SERVICES: tuple[ServiceQualification, ...] = (
    OpenAIQualification(),
    ClaudeQualification(),
    GeminiQualification(),
)


def service_qualifications() -> tuple[ServiceQualification, ...]:
    """Return the ordered built-in service registry."""

    return _DEFAULT_SERVICES


def service_qualification_by_probe(probe_name: str) -> ServiceQualification:
    for service in service_qualifications():
        if service.probe_name == probe_name:
            return service
    raise ValidationError(f"no ServiceQualification implementation for {probe_name!r}")


def service_order() -> tuple[str, ...]:
    return tuple(service.probe_name for service in service_qualifications())


def service_labels() -> dict[str, str]:
    return {service.probe_name: service.label for service in service_qualifications()}


def service_targets() -> dict[str, str]:
    return {service.probe_name: service.target_group for service in service_qualifications()}


def apply_service_route_postprocessing(candidate: Path) -> dict[str, Any]:
    """Run optional provider-owned routing post-processors without vendor branching."""

    reports: dict[str, Any] = {}
    for service in service_qualifications():
        result = service.route_postprocess(candidate)
        if result is not None:
            key, report = result
            reports[key] = report
    return reports


def harden_declared_service_client_paths(*, candidate: Path, policies: Path) -> dict[str, Any]:
    """Apply only declaratively enabled client-path hardeners from the registry."""

    document = load_policy_document(policies).document
    probes = document.get("probes")
    if not isinstance(probes, dict):
        raise ValidationError("service client-path hardening requires policy probes")

    known = {service.probe_name: service for service in service_qualifications()}
    reports: dict[str, Any] = {}
    for probe_name, probe in probes.items():
        if not isinstance(probe, dict) or probe.get("client_path_hardening") is not True:
            continue
        service = known.get(str(probe_name))
        if service is None:
            raise ValidationError(
                f"client-path hardening declared for unsupported service {probe_name!r}"
            )
        if not service.supports_client_path_hardening:
            raise ValidationError(
                f"service {probe_name!r} does not implement declared client-path hardening"
            )
        reports[service.label] = service.harden_client_path(candidate)

    return {
        "status": "passed",
        "hardened_services": len(reports),
        "services": reports,
    }
