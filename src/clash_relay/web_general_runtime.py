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
from .regional_web_runtime import drop_unused_pool_scaffold, proxy_identity, regional_groups
from .runtime_graph import RuntimeGraph
from .util import atomic_write, dump_yaml, load_yaml_file

WEB_GENERAL_AUTO_GROUP = "网页通用自动"
WEB_GENERAL_PROVIDER_PREFIX = "cr_web_general_"
_POOL_GROUP = "__CR_WEB_GENERAL_INVENTORY"
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
    general_leaves: dict[str, tuple[str, str]] = {}
    for name, provider in providers.items():
        if not str(name).startswith("cr_general_"):
            continue
        for proxy in provider.get("payload", []):
            general_leaves.setdefault(proxy_identity(proxy), (str(name), str(proxy["name"])))
    if not general_leaves:
        raise GenerationError("general web runtime cannot resolve the general inventory")
    for region in available:
        provider_name = by_region[region]
        provider = providers[provider_name]
        try:
            mapped = [general_leaves[proxy_identity(proxy)] for proxy in provider["payload"]]
        except KeyError as exc:
            raise GenerationError("general web node is absent from the general inventory") from exc
        shared_providers = sorted({name for name, _ in mapped})
        node_names = {name for _, name in mapped}
        for group in regional_groups(
            provider_names=shared_providers,
            stable_name=_stable_group(region),
            reserve_name=_reserve_group(region),
            region_name=_region_group(region),
            node_filter=_exact_filter(node_names),
            scheduler_fields=scheduler_fields,
            region_fields=region_fields,
        ):
            _replace(groups, str(group["name"]), group)
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
    drop_unused_pool_scaffold(config, pool="WEB_GENERAL", inventory=_POOL_GROUP)
    for name in by_region.values():
        if any(name in group.get("use", []) for group in groups):
            raise GenerationError("general web draft provider remains referenced")
        providers.pop(name)
    validate_web_general_runtime(config)
    return {
        "status": "regional_hardened",
        "group": WEB_GENERAL_AUTO_GROUP,
        "available_regions": available,
        "source_use": "general",
        "probe": str(probe["url"]),
        "shared_general_providers": True,
        "providers_removed": len(by_region),
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
        if (
            not isinstance(regional, dict)
            or regional.get("type") != "fallback"
            or regional.get("proxies") != [_stable_group(region), _reserve_group(region)]
            or regional.get("hidden") is not True
        ):
            raise ValidationError("general web region lost its same-region fallback")
        for tier in (stable, reserve):
            if (
                not isinstance(tier, dict)
                or tier.get("type") != "url-test"
                or tier.get("hidden") is not True
                or not isinstance(tier.get("use"), list)
                or not tier["use"]
                or any(
                    not str(name).startswith("cr_general_") or name not in providers
                    for name in tier["use"]
                )
                or (
                    not str(tier.get("filter", "")).startswith("^(")
                    and not (tier.get("filter") == "^$" and tier.get("proxies") == ["REJECT"])
                )
                or not str(tier.get("url", "")).startswith("https://")
            ):
                raise ValidationError("general web tier lost its general-only provider")


def web_general_runtime_names(config: dict[str, Any]) -> set[str]:
    if WEB_GENERAL_AUTO_GROUP not in _groups_by_name(config):
        return set()
    return set(RuntimeGraph.from_candidate(config).effective_leaf_proxies(WEB_GENERAL_AUTO_GROUP))


def rewrite_web_general_qualified_candidate(
    candidate_path: Path,
    qualified_names: set[str],
    stable_names: set[str],
    preferred_names: set[str],
    *,
    validate_candidate: bool = True,
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
    graph = RuntimeGraph.from_candidate(config)
    for region in REGION_LABELS:
        if _region_group(region) not in automatic["proxies"]:
            continue
        regional_names = set(graph.effective_leaf_proxies(_region_group(region)))
        tested += len(regional_names)
        names = regional_names & qualified_names
        if not names:
            removed_groups.update(
                {_region_group(region), _stable_group(region), _reserve_group(region)}
            )
            continue
        qualified += len(names)
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

    if validate_candidate:
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
        "regional_scheduler": "passed",
        "qualification": "passed",
        "shared_general_providers": True,
    }


def _comment_header(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")
