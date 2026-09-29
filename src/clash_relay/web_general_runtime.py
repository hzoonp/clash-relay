"""Browsing-grade regional scheduling over general-only web providers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .browsing_regions import REGION_LABELS, normalize_region
from .browsing_runtime import (
    _browsing_probe,
    _exact_filter,
    _groups_by_name,
    _region_switch_interval,
    _runtime_test_fields,
)
from .errors import GenerationError, ValidationError
from .util import atomic_write, dump_yaml, load_yaml_file

WEB_GENERAL_AUTO_GROUP = "网页通用自动"
WEB_GENERAL_PROVIDER_PREFIX = "cr_web_general_"
_POOL_GROUP = "__CR_WEB_GENERAL_INVENTORY"
_AUTO_PREFIX = "__CR_AUTO_WEB_GENERAL_"
_FALLBACK_GROUP = "__CR_FALLBACK_WEB_GENERAL"
_MIN_PREFERRED_STABLE_NODES = 3


def provider_region(name: str) -> str | None:
    if not name.startswith(WEB_GENERAL_PROVIDER_PREFIX):
        return None
    region = name[len(WEB_GENERAL_PROVIDER_PREFIX) :].upper()
    return region if region in REGION_LABELS else None


def _region_group(region: str) -> str:
    code = normalize_region(region)
    return f"网页通用 · {REGION_LABELS[code]}"


def _stable_group(region: str) -> str:
    return f"__CR_WEB_GENERAL_{normalize_region(region)}_STABLE_AUTO"


def _reserve_group(region: str) -> str:
    return f"__CR_WEB_GENERAL_{normalize_region(region)}_RESERVE_AUTO"


def _preferred_regions(policies: dict[str, Any]) -> list[str]:
    routing = policies.get("routing")
    browsing = routing.get("browsing") if isinstance(routing, dict) else None
    preferred = browsing.get("preferred_regions") if isinstance(browsing, dict) else None
    pool = next((row for row in policies.get("pools", []) if row.get("id") == "web_general"), None)
    if not isinstance(preferred, list) or not isinstance(pool, dict):
        raise GenerationError("general web runtime requires declared browsing regions and pool")
    regions = [str(region).upper() for region in preferred]
    if (
        not regions
        or pool.get("source_use") != "general"
        or pool.get("probe") != "browsing"
        or pool.get("regions") != regions
        or pool.get("fallback_order") != regions
    ):
        raise GenerationError("general web pool must mirror browsing regions using general")
    return regions


def _replace(groups: list[dict[str, Any]], name: str, value: dict[str, Any]) -> None:
    for group in groups:
        if isinstance(group, dict) and group.get("name") == name:
            group.clear()
            group.update(value)
            return
    groups.append(value)


def harden_web_general_runtime(config: dict[str, Any], policies: dict[str, Any]) -> dict[str, Any]:
    groups = config.get("proxy-groups")
    providers = config.get("proxy-providers")
    if not isinstance(groups, list) or not isinstance(providers, dict):
        raise GenerationError("general web runtime requires generated groups and providers")
    automatic = _groups_by_name(config).get(WEB_GENERAL_AUTO_GROUP)
    if automatic is None:
        return {"status": "not_applicable"}
    uses = automatic.get("use")
    if not isinstance(uses, list) or not uses:
        raise GenerationError("general web automatic group has no provider inventory")
    preferred_regions = _preferred_regions(policies)
    by_region: dict[str, str] = {}
    for raw_name in uses:
        provider_name = str(raw_name)
        region = provider_region(provider_name)
        if region is None or region not in preferred_regions or provider_name not in providers:
            raise GenerationError(
                "general web automatic group references a non-general web provider"
            )
        if region in by_region:
            raise GenerationError("general web runtime has duplicate regional providers")
        by_region[region] = provider_name
    probe = _browsing_probe(policies)
    scheduler_fields = _runtime_test_fields(probe, tolerance=True)
    region_fields = _runtime_test_fields(probe, tolerance=False)
    available = [region for region in preferred_regions if region in by_region]
    for region in available:
        provider_name = by_region[region]
        provider = providers[provider_name]
        provider_fields = _runtime_test_fields(probe, tolerance=False)
        provider["health-check"] = {
            "enable": True,
            "url": provider_fields["url"],
            "interval": provider_fields["interval"],
            "timeout": provider_fields["timeout"],
            "lazy": provider_fields["lazy"],
            "expected-status": provider_fields["expected-status"],
        }
        for name in (_stable_group(region), _reserve_group(region)):
            _replace(
                groups,
                name,
                {
                    "name": name,
                    "type": "url-test",
                    "hidden": True,
                    "use": [provider_name],
                    "filter": ".*",
                    **scheduler_fields,
                },
            )
        _replace(
            groups,
            _region_group(region),
            {
                "name": _region_group(region),
                "type": "fallback",
                "hidden": True,
                "proxies": [_stable_group(region), _reserve_group(region)],
                **region_fields,
            },
        )
    automatic.clear()
    automatic.update(
        {
            "name": WEB_GENERAL_AUTO_GROUP,
            "type": "url-test",
            "hidden": True,
            "proxies": [_region_group(region) for region in available],
            **_runtime_test_fields(
                probe, tolerance=True, interval=_region_switch_interval(policies, probe)
            ),
        }
    )
    # The pool scaffold is only needed to resolve regional providers. The
    # dedicated group now owns the entire general-only web route.
    groups[:] = [
        group
        for group in groups
        if not (
            isinstance(group, dict)
            and (
                str(group.get("name", "")).startswith(_AUTO_PREFIX)
                or group.get("name") in {_POOL_GROUP, _FALLBACK_GROUP}
            )
        )
    ]
    validate_web_general_runtime(config)
    return {
        "status": "regional_hardened",
        "group": WEB_GENERAL_AUTO_GROUP,
        "available_regions": available,
        "source_use": "general",
        "probe": str(probe["url"]),
    }


def validate_web_general_runtime(config: dict[str, Any]) -> None:
    groups = _groups_by_name(config)
    automatic = groups.get(WEB_GENERAL_AUTO_GROUP)
    if automatic is None:
        return
    references = automatic.get("proxies")
    if (
        automatic.get("type") != "url-test"
        or automatic.get("hidden") is not True
        or automatic.get("use")
        or not isinstance(references, list)
        or not references
        or not str(automatic.get("url", "")).startswith("https://")
    ):
        raise ValidationError("general web automatic group lost its regional URLTest contract")
    providers = config.get("proxy-providers")
    if not isinstance(providers, dict):
        raise ValidationError("general web runtime requires proxy providers")
    seen: set[str] = set()
    for region_name in references:
        region = next((code for code in REGION_LABELS if _region_group(code) == region_name), None)
        if region is None or region in seen:
            raise ValidationError("general web automatic group has an invalid region")
        seen.add(region)
        regional = groups.get(str(region_name))
        stable = groups.get(_stable_group(region))
        reserve = groups.get(_reserve_group(region))
        provider = WEB_GENERAL_PROVIDER_PREFIX + region.lower()
        if (
            not isinstance(regional, dict)
            or regional.get("type") != "fallback"
            or regional.get("proxies") != [_stable_group(region), _reserve_group(region)]
            or regional.get("hidden") is not True
            or provider not in providers
        ):
            raise ValidationError("general web region lost its same-region fallback")
        for tier in (stable, reserve):
            if (
                not isinstance(tier, dict)
                or tier.get("type") != "url-test"
                or tier.get("hidden") is not True
                or tier.get("use") != [provider]
                or not str(tier.get("url", "")).startswith("https://")
            ):
                raise ValidationError("general web tier lost its general-only provider")


def web_general_runtime_names(config: dict[str, Any]) -> set[str]:
    providers = config.get("proxy-providers")
    if not isinstance(providers, dict):
        raise ValidationError("general web runtime requires proxy providers")
    return {
        str(proxy["name"])
        for name, provider in providers.items()
        if provider_region(str(name)) is not None
        for proxy in provider.get("payload", [])
        if isinstance(proxy, dict) and isinstance(proxy.get("name"), str)
    }


def rewrite_web_general_qualified_candidate(
    candidate_path: Path,
    qualified_names: set[str],
    stable_names: set[str],
    preferred_names: set[str],
) -> dict[str, Any]:
    original = candidate_path.read_text(encoding="utf-8")
    config = load_yaml_file(candidate_path)
    if not isinstance(config, dict):
        raise ValidationError("general web qualification candidate is invalid")
    providers = config.get("proxy-providers")
    groups = config.get("proxy-groups")
    if not isinstance(providers, dict) or not isinstance(groups, list):
        raise ValidationError("general web qualification requires providers and groups")
    automatic = _groups_by_name(config).get(WEB_GENERAL_AUTO_GROUP)
    if not isinstance(automatic, dict):
        return {"status": "not_applicable"}
    tested = 0
    qualified = 0
    available: list[str] = []
    removed_groups: set[str] = set()
    for provider_name in list(providers):
        region = provider_region(str(provider_name))
        if region is None:
            continue
        provider = providers[provider_name]
        payload = provider.get("payload") if isinstance(provider, dict) else None
        if not isinstance(payload, list):
            raise ValidationError("general web provider payload is invalid")
        tested += len(payload)
        kept = [
            proxy
            for proxy in payload
            if isinstance(proxy, dict) and proxy.get("name") in qualified_names
        ]
        if not kept:
            providers.pop(provider_name)
            removed_groups.update(
                {_region_group(region), _stable_group(region), _reserve_group(region)}
            )
            continue
        provider["payload"] = kept
        qualified += len(kept)
        names = {str(proxy["name"]) for proxy in kept}
        by_name = _groups_by_name(config)
        stable_group = by_name[_stable_group(region)]
        cap_filter = str(stable_group.get("filter", ".*"))
        cap_allowed = {name for name in names if re.search(cap_filter, name)}
        stable = stable_names & cap_allowed
        preferred = preferred_names & stable
        effective_stable = (
            preferred if len(preferred) >= _MIN_PREFERRED_STABLE_NODES else stable or cap_allowed
        )
        if stable_group.get("proxies") == ["REJECT"] or not effective_stable:
            stable_group["filter"] = "^$"
            stable_group["proxies"] = ["REJECT"]
            reserve = names
        else:
            stable_group["filter"] = _exact_filter(effective_stable)
            reserve = names - effective_stable or names
        by_name[_reserve_group(region)]["filter"] = _exact_filter(reserve)
        available.append(region)
    if not available:
        raise ValidationError("general web qualification retained no general-only node")
    groups[:] = [
        group
        for group in groups
        if not (isinstance(group, dict) and group.get("name") in removed_groups)
    ]
    automatic["proxies"] = [name for name in automatic["proxies"] if name not in removed_groups]
    validate_web_general_runtime(config)
    from .validator import validate_generated_config

    validate_generated_config(config)
    atomic_write(candidate_path, _comment_header(original) + dump_yaml(config))
    return {
        "status": "qualified",
        "tested_nodes": tested,
        "qualified_nodes": qualified,
        "removed_regions": sorted(
            region for region in REGION_LABELS if _region_group(region) in removed_groups
        ),
        "available_regions": available,
        "source_use": "general",
    }


def _comment_header(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")
