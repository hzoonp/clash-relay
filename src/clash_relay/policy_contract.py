"""Declarative runtime naming/fidelity contract for Routing Model V2.

There is deliberately no Python fallback contract.  Any consumer that requires
routing semantics must receive an explicit ``routing.contract`` declaration and
fails closed otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class AiPolicyContract:
    service_targets: dict[str, str]
    service_prefixes: dict[str, str]
    region_display_names: dict[str, tuple[str, ...]]
    required_excluded_regions: tuple[str, ...]

    @property
    def canonical_region_display(self) -> dict[str, str]:
        return {region: names[0] for region, names in self.region_display_names.items() if names}

    @property
    def display_region_codes(self) -> dict[str, str]:
        return {
            display_name: region
            for region, display_names in self.region_display_names.items()
            for display_name in display_names
        }

    def region_for_display(self, display_name: str) -> str | None:
        return self.display_region_codes.get(display_name)


@dataclass(frozen=True, slots=True)
class RuntimePolicyContract:
    declared: bool
    public_groups: dict[str, str]
    automatic_groups: dict[str, str]
    general_region_choices: tuple[str, ...]
    compatibility_selectors: dict[str, tuple[str, ...]]
    disabled_groups: tuple[str, ...]
    ai: AiPolicyContract
    binding_targets: dict[str, str]
    priority_edges: tuple[tuple[str, str], ...]
    acl4ssr_baseline: str
    intentional_deviations: tuple[str, ...]

    @property
    def visible_groups(self) -> frozenset[str]:
        return frozenset(self.public_groups.values())

    def public_group(self, purpose: str) -> str:
        try:
            return self.public_groups[purpose]
        except KeyError as exc:
            raise ConfigurationError(
                f"routing contract has no public group for purpose {purpose!r}"
            ) from exc

    def automatic_group(self, purpose: str) -> str:
        try:
            return self.automatic_groups[purpose]
        except KeyError as exc:
            raise ConfigurationError(
                f"routing contract has no automatic group for purpose {purpose!r}"
            ) from exc

    def binding_target(self, source_id: str) -> str:
        try:
            return self.binding_targets[source_id]
        except KeyError as exc:
            raise ConfigurationError(
                f"routing contract has no target for binding {source_id!r}"
            ) from exc


def _string_mapping(value: Any, *, field: str) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise ConfigurationError(f"routing contract {field} must be a non-empty mapping")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or not isinstance(item, str) or not item:
            raise ConfigurationError(f"routing contract {field} must contain non-empty strings")
        result[key] = item
    return result


def _string_list(value: Any, *, field: str, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ConfigurationError(f"routing contract {field} must be a string list")
    items = tuple(str(item) for item in value if isinstance(item, str) and item)
    if len(items) != len(value) or len(items) != len(set(items)):
        raise ConfigurationError(f"routing contract {field} must contain unique non-empty strings")
    return items


def load_policy_contract(policies: dict[str, Any]) -> RuntimePolicyContract:
    routing = policies.get("routing")
    if not isinstance(routing, dict):
        raise ConfigurationError("routing policy and routing.contract are required")
    document = routing.get("contract")
    if document is None:
        raise ConfigurationError("routing.contract is required")
    if not isinstance(document, dict):
        raise ConfigurationError("routing contract must be a mapping")

    public_groups = _string_mapping(document.get("public_groups"), field="public_groups")
    required_public = {"general", "browsing", "ai", "media", "messaging", "download"}
    if set(public_groups) != required_public:
        raise ConfigurationError(
            "routing contract public_groups must declare exactly: "
            + ", ".join(sorted(required_public))
        )

    automatic_groups = _string_mapping(document.get("automatic_groups"), field="automatic_groups")
    required_automatic = {"media", "messaging", "download"}
    if set(automatic_groups) != required_automatic:
        raise ConfigurationError(
            "routing contract automatic_groups must declare media, messaging, and download"
        )

    compatibility_raw = document.get("compatibility_selectors")
    if not isinstance(compatibility_raw, dict) or not compatibility_raw:
        raise ConfigurationError("routing contract compatibility_selectors must be a mapping")
    compatibility = {
        str(name): _string_list(members, field=f"compatibility_selectors.{name}")
        for name, members in compatibility_raw.items()
        if isinstance(name, str) and name
    }
    if len(compatibility) != len(compatibility_raw):
        raise ConfigurationError("routing contract compatibility selector names must be strings")

    ai_raw = document.get("ai")
    if not isinstance(ai_raw, dict):
        raise ConfigurationError("routing contract ai must be a mapping")
    region_raw = ai_raw.get("region_display_names")
    if not isinstance(region_raw, dict) or not region_raw:
        raise ConfigurationError("routing contract ai.region_display_names must be a mapping")
    region_display_names = {
        str(region): _string_list(names, field=f"ai.region_display_names.{region}")
        for region, names in region_raw.items()
        if isinstance(region, str) and region
    }
    if len(region_display_names) != len(region_raw):
        raise ConfigurationError("routing contract region names must be strings")

    all_region_displays = [
        display_name
        for display_names in region_display_names.values()
        for display_name in display_names
    ]
    if len(all_region_displays) != len(set(all_region_displays)):
        raise ConfigurationError(
            "routing contract AI region display aliases must be globally unique"
        )

    binding_targets = _string_mapping(document.get("binding_targets"), field="binding_targets")
    priority_raw = document.get("priority_edges")
    if not isinstance(priority_raw, list) or not priority_raw:
        raise ConfigurationError("routing contract priority_edges must be a non-empty list")
    priority_edges: list[tuple[str, str]] = []
    for edge in priority_raw:
        if (
            not isinstance(edge, list)
            or len(edge) != 2
            or not all(isinstance(item, str) and item for item in edge)
        ):
            raise ConfigurationError(
                "routing contract priority_edges entries must be [before, after]"
            )
        pair = (str(edge[0]), str(edge[1]))
        if pair[0] not in binding_targets or pair[1] not in binding_targets:
            raise ConfigurationError("routing contract priority_edges reference unknown bindings")
        priority_edges.append(pair)

    return RuntimePolicyContract(
        declared=True,
        public_groups=public_groups,
        automatic_groups=automatic_groups,
        general_region_choices=_string_list(
            document.get("general_region_choices"), field="general_region_choices"
        ),
        compatibility_selectors=compatibility,
        disabled_groups=_string_list(
            document.get("disabled_groups", []), field="disabled_groups", allow_empty=True
        ),
        ai=AiPolicyContract(
            service_targets=_string_mapping(
                ai_raw.get("service_targets"), field="ai.service_targets"
            ),
            service_prefixes=_string_mapping(
                ai_raw.get("service_prefixes"), field="ai.service_prefixes"
            ),
            region_display_names=region_display_names,
            required_excluded_regions=_string_list(
                ai_raw.get("required_excluded_regions", []),
                field="ai.required_excluded_regions",
                allow_empty=True,
            ),
        ),
        binding_targets=binding_targets,
        priority_edges=tuple(priority_edges),
        acl4ssr_baseline=str(document.get("acl4ssr_baseline", "")),
        intentional_deviations=_string_list(
            document.get("intentional_deviations", []),
            field="intentional_deviations",
            allow_empty=True,
        ),
    )


def policy_contract_summary(contract: RuntimePolicyContract) -> dict[str, Any]:
    return {
        "declared": contract.declared,
        "public_groups": dict(contract.public_groups),
        "automatic_groups": dict(contract.automatic_groups),
        "required_ai_exclusions": list(contract.ai.required_excluded_regions),
        "compatibility_selectors": len(contract.compatibility_selectors),
        "bindings": len(contract.binding_targets),
        "priority_edges": len(contract.priority_edges),
    }
