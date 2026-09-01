"""Declarative scenario/scheduler policy for Routing Model V2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import ConfigurationError
from .policy_contract import load_policy_contract, policy_contract_summary

_REQUIRED_SCENARIOS = ("direct", "general", "browsing", "media", "download", "ai", "final")
_ALLOWED_PROFILES = frozenset({"direct", "connectivity", "browsing", "media", "download", "ai"})
_ALLOWED_DOWNLOAD_MODES = frozenset({"direct", "general_auto"})


@dataclass(frozen=True, slots=True)
class ScenarioPolicy:
    source_use: str
    scheduler_profile: str


@dataclass(frozen=True, slots=True)
class AiRoutingPolicy:
    excluded_regions: tuple[str, ...]
    preferred_regions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DownloadRoutingPolicy:
    mode: str


@dataclass(frozen=True, slots=True)
class RoutingPolicyV2:
    declared: bool
    scenarios: dict[str, ScenarioPolicy]
    ai: AiRoutingPolicy
    download: DownloadRoutingPolicy

    def scenario_use(self, scenario: str) -> str:
        try:
            return self.scenarios[scenario].source_use
        except KeyError as exc:
            raise ConfigurationError(f"routing v2 has no policy for scenario {scenario!r}") from exc


def _default_document() -> dict[str, Any]:
    return {
        "version": 2,
        "scenarios": {
            "direct": {"source_use": "general", "scheduler_profile": "direct"},
            "general": {"source_use": "general", "scheduler_profile": "connectivity"},
            "browsing": {"source_use": "browsing", "scheduler_profile": "browsing"},
            "media": {"source_use": "general", "scheduler_profile": "media"},
            "download": {"source_use": "general", "scheduler_profile": "download"},
            "ai": {"source_use": "ai", "scheduler_profile": "ai"},
            "final": {"source_use": "general", "scheduler_profile": "connectivity"},
        },
        "ai": {
            "excluded_regions": ["HK"],
            "preferred_regions": ["US", "SG", "JP", "TW", "KR", "OTHER"],
        },
        "download": {"mode": "direct"},
    }


def load_routing_policy_v2(policies: dict[str, Any]) -> RoutingPolicyV2:
    """Normalize the optional Routing V2 block with safe v1-compatible defaults."""

    raw = policies.get("routing")
    declared = raw is not None
    document = _default_document() if raw is None else raw
    if not isinstance(document, dict) or int(document.get("version", 0)) != 2:
        raise ConfigurationError("routing policy must declare version 2")

    raw_scenarios = document.get("scenarios")
    if not isinstance(raw_scenarios, dict):
        raise ConfigurationError("routing policy scenarios must be a mapping")
    if set(raw_scenarios) != set(_REQUIRED_SCENARIOS):
        raise ConfigurationError(
            "routing policy must declare exactly: " + ", ".join(_REQUIRED_SCENARIOS)
        )

    scenarios: dict[str, ScenarioPolicy] = {}
    for name in _REQUIRED_SCENARIOS:
        row = raw_scenarios[name]
        if not isinstance(row, dict):
            raise ConfigurationError(f"routing scenario {name!r} must be a mapping")
        source_use = str(row.get("source_use", ""))
        profile = str(row.get("scheduler_profile", ""))
        if not source_use:
            raise ConfigurationError(f"routing scenario {name!r} has no source_use")
        if profile not in _ALLOWED_PROFILES:
            raise ConfigurationError(
                f"routing scenario {name!r} uses unknown scheduler profile {profile!r}"
            )
        scenarios[name] = ScenarioPolicy(source_use=source_use, scheduler_profile=profile)

    raw_ai = document.get("ai")
    if not isinstance(raw_ai, dict):
        raise ConfigurationError("routing ai policy must be a mapping")
    excluded = tuple(str(item) for item in raw_ai.get("excluded_regions", []))
    preferred = tuple(str(item) for item in raw_ai.get("preferred_regions", []))
    if len(excluded) != len(set(excluded)) or len(preferred) != len(set(preferred)):
        raise ConfigurationError("routing ai regions must not contain duplicates")
    if set(excluded) & set(preferred):
        raise ConfigurationError("routing ai preferred regions cannot include excluded regions")
    contract = load_policy_contract(policies)
    required_excluded = set(contract.ai.required_excluded_regions)
    if not required_excluded <= set(excluded):
        missing = ", ".join(sorted(required_excluded - set(excluded)))
        raise ConfigurationError(
            f"routing ai policy is missing contract-required excluded regions: {missing}"
        )
    if not preferred:
        raise ConfigurationError("routing ai policy requires at least one preferred region")

    raw_download = document.get("download")
    if not isinstance(raw_download, dict):
        raise ConfigurationError("routing download policy must be a mapping")
    download_mode = str(raw_download.get("mode", ""))
    if download_mode not in _ALLOWED_DOWNLOAD_MODES:
        raise ConfigurationError(f"unknown routing download mode {download_mode!r}")

    return RoutingPolicyV2(
        declared=declared,
        scenarios=scenarios,
        ai=AiRoutingPolicy(excluded_regions=excluded, preferred_regions=preferred),
        download=DownloadRoutingPolicy(mode=download_mode),
    )


def routing_policy_summary(
    policy: RoutingPolicyV2, policies: dict[str, Any] | None = None
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "version": 2,
        "declared": policy.declared,
        "scenarios": {
            name: {
                "source_use": row.source_use,
                "scheduler_profile": row.scheduler_profile,
            }
            for name, row in policy.scenarios.items()
        },
        "ai": {
            "excluded_regions": list(policy.ai.excluded_regions),
            "preferred_regions": list(policy.ai.preferred_regions),
        },
        "download": {"mode": policy.download.mode},
    }
    if policies is not None:
        summary["contract"] = policy_contract_summary(load_policy_contract(policies))
    return summary
