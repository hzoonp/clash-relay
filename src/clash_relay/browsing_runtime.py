"""Regional runtime hardening for the canonical browsing scenario.

The generated browsing inventory is provider-backed, but public scenario
selectors never expose those providers or raw runtime nodes. Browsing is
scheduled in two dimensions: first by preferred region, then by Stable/Reserve
quality inside that region.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .browsing_regions import (
    BROWSING_PROVIDER_PREFIX,
    provider_region,
    region_display_name,
    region_from_display_name,
    region_reserve_group,
    region_stable_group,
)
from .errors import GenerationError, ValidationError
from .util import atomic_write, dump_yaml, load_yaml_file, normalize_expected_status

BROWSING_PUBLIC_GROUP = "网页浏览"
BROWSING_AUTO_GROUP = "网页自动"
CANONICAL_PUBLIC_GROUPS = frozenset({"代理选择", BROWSING_PUBLIC_GROUP, "人工智能"})
_MIN_PREFERRED_STABLE_NODES = 3
_RE2_META = frozenset("\\.+*?()|[]{}^$")


def _groups_by_name(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    groups = config.get("proxy-groups", [])
    if not isinstance(groups, list):
        raise ValidationError("browsing runtime requires proxy-groups to be a list")
    return {
        str(group["name"]): group
        for group in groups
        if isinstance(group, dict) and isinstance(group.get("name"), str)
    }


def _browsing_pool(policies: dict[str, Any]) -> dict[str, Any]:
    pools = policies.get("pools")
    if not isinstance(pools, list):
        raise GenerationError("browsing runtime requires policy pools")
    pool = next(
        (item for item in pools if isinstance(item, dict) and item.get("id") == "browsing"),
        None,
    )
    if not isinstance(pool, dict):
        raise GenerationError("browsing runtime requires the canonical browsing pool")
    return pool


def _browsing_probe(policies: dict[str, Any]) -> dict[str, Any]:
    pool = _browsing_pool(policies)
    probes = policies.get("probes")
    if not isinstance(probes, dict):
        raise GenerationError("browsing runtime requires policy probes")
    probe_name = pool.get("probe")
    probe = probes.get(probe_name) if isinstance(probe_name, str) else None
    if not isinstance(probe, dict):
        raise GenerationError("browsing runtime probe is missing")
    url = probe.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise GenerationError("canonical browsing runtime probe must use HTTPS")
    return probe


def _preferred_regions(policies: dict[str, Any]) -> list[str]:
    routing = policies.get("routing")
    browsing = routing.get("browsing") if isinstance(routing, dict) else None
    preferred = browsing.get("preferred_regions") if isinstance(browsing, dict) else None
    if not isinstance(preferred, list) or not preferred:
        raise GenerationError("routing.browsing.preferred_regions must be declared")
    regions = [str(region).upper() for region in preferred]
    if len(set(regions)) != len(regions):
        raise GenerationError("routing.browsing.preferred_regions contains duplicates")

    pool = _browsing_pool(policies)
    pool_regions = [str(region).upper() for region in pool.get("regions", [])]
    fallback_order = [str(region).upper() for region in pool.get("fallback_order", [])]
    if set(pool_regions) != set(regions) or fallback_order != regions:
        raise GenerationError(
            "canonical browsing pool regions/fallback_order must match "
            "routing.browsing.preferred_regions"
        )
    return regions


def _region_switch_interval(policies: dict[str, Any], probe: dict[str, Any]) -> int:
    scheduler = policies.get("scheduler")
    browsing = scheduler.get("browsing") if isinstance(scheduler, dict) else None
    value = browsing.get("region_switch_interval") if isinstance(browsing, dict) else None
    if value is None:
        return int(probe["interval"])
    interval = int(value)
    if interval < int(probe["interval"]):
        raise GenerationError(
            "scheduler.browsing.region_switch_interval must be at least the browsing probe interval"
        )
    return interval


def _runtime_test_fields(
    probe: dict[str, Any], *, tolerance: bool, interval: int | None = None
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "url": str(probe["url"]),
        "interval": int(probe["interval"] if interval is None else interval),
        "timeout": int(probe["timeout"]),
        "lazy": bool(probe["lazy"]),
        "expected-status": normalize_expected_status(probe["expected_status"]),
    }
    if tolerance:
        fields["tolerance"] = int(probe["tolerance"])
    return fields


def _quote_re2_literal(value: str) -> str:
    return "".join(f"\\{character}" if character in _RE2_META else character for character in value)


def _exact_filter(names: set[str]) -> str:
    if not names:
        raise ValidationError("browsing runtime cannot create an empty node filter")
    return "^(" + "|".join(_quote_re2_literal(name) for name in sorted(names)) + ")$"


def _replace_or_append_group(
    groups: list[dict[str, Any]],
    by_name: dict[str, dict[str, Any]],
    name: str,
    value: dict[str, Any],
) -> None:
    current = by_name.get(name)
    if current is None:
        groups.append(value)
        by_name[name] = value
        return
    current.clear()
    current.update(value)


def _providers_by_region(
    provider_names: list[str], providers: dict[str, Any], preferred_regions: list[str]
) -> dict[str, str]:
    result: dict[str, str] = {}
    for provider_name in provider_names:
        if provider_name not in providers or not provider_name.startswith(BROWSING_PROVIDER_PREFIX):
            raise GenerationError(
                "canonical browsing automatic group references a non-browsing provider"
            )
        region = provider_region(provider_name)
        if region is None or region not in preferred_regions:
            raise GenerationError(
                f"browsing provider {provider_name!r} has an undeclared regional scope"
            )
        if region in result:
            raise GenerationError(
                f"multiple canonical browsing providers were generated for {region}"
            )
        result[region] = provider_name
    if not result:
        raise GenerationError("canonical browsing runtime resolved no regional providers")
    return result


def harden_browsing_runtime(config: dict[str, Any], policies: dict[str, Any]) -> dict[str, Any]:
    """Create region-priority browsing failover while keeping providers private."""

    groups = config.get("proxy-groups")
    providers = config.get("proxy-providers")
    if not isinstance(groups, list) or not isinstance(providers, dict):
        raise GenerationError("browsing runtime requires generated groups and providers")
    by_name = _groups_by_name(config)
    public = by_name.get(BROWSING_PUBLIC_GROUP)
    automatic = by_name.get(BROWSING_AUTO_GROUP)
    if public is None and automatic is None:
        return {"status": "not_applicable"}
    if public is None or automatic is None:
        raise GenerationError("canonical browsing runtime groups are incomplete")

    uses = automatic.get("use")
    if not isinstance(uses, list) or not uses:
        raise GenerationError("canonical browsing automatic group has no provider inventory")
    provider_names = [str(name) for name in uses]
    preferred_regions = _preferred_regions(policies)
    providers_by_region = _providers_by_region(provider_names, providers, preferred_regions)
    available_regions = [region for region in preferred_regions if region in providers_by_region]

    probe = _browsing_probe(policies)
    provider_fields = _runtime_test_fields(probe, tolerance=False)
    scheduler_fields = _runtime_test_fields(probe, tolerance=True)
    region_fields = _runtime_test_fields(probe, tolerance=False)
    for region in available_regions:
        provider_name = providers_by_region[region]
        provider = providers[provider_name]
        if not isinstance(provider, dict):
            raise GenerationError("canonical browsing provider is invalid")
        provider["health-check"] = {
            "enable": True,
            "url": provider_fields["url"],
            "interval": provider_fields["interval"],
            "timeout": provider_fields["timeout"],
            "lazy": provider_fields["lazy"],
            "expected-status": provider_fields["expected-status"],
        }

        stable_name = region_stable_group(region)
        reserve_name = region_reserve_group(region)
        region_name = region_display_name(region)
        _replace_or_append_group(
            groups,
            by_name,
            stable_name,
            {
                "name": stable_name,
                "type": "url-test",
                "hidden": True,
                "use": [provider_name],
                "filter": ".*",
                **scheduler_fields,
            },
        )
        _replace_or_append_group(
            groups,
            by_name,
            reserve_name,
            {
                "name": reserve_name,
                "type": "url-test",
                "hidden": True,
                "use": [provider_name],
                "filter": ".*",
                **scheduler_fields,
            },
        )
        _replace_or_append_group(
            groups,
            by_name,
            region_name,
            {
                "name": region_name,
                "type": "fallback",
                "hidden": True,
                "proxies": [stable_name, reserve_name],
                **region_fields,
            },
        )

    region_groups = [region_display_name(region) for region in available_regions]
    automatic.clear()
    automatic.update(
        {
            "name": BROWSING_AUTO_GROUP,
            "type": "fallback",
            "hidden": True,
            "proxies": region_groups,
            **_runtime_test_fields(
                probe,
                tolerance=False,
                interval=_region_switch_interval(policies, probe),
            ),
        }
    )
    public.clear()
    public.update(
        {
            "name": BROWSING_PUBLIC_GROUP,
            "type": "select",
            "proxies": [BROWSING_AUTO_GROUP, *region_groups, "DIRECT"],
        }
    )

    validate_browsing_public_surface(config)
    return {
        "status": "regional_hardened",
        "public_group": BROWSING_PUBLIC_GROUP,
        "automatic_group": BROWSING_AUTO_GROUP,
        "preferred_regions": preferred_regions,
        "available_regions": available_regions,
        "region_switch_interval": automatic["interval"],
        "providers": [providers_by_region[region] for region in available_regions],
        "probe": str(probe["url"]),
    }


def _runtime_regions(config: dict[str, Any]) -> list[str]:
    by_name = _groups_by_name(config)
    automatic = by_name.get(BROWSING_AUTO_GROUP)
    if not isinstance(automatic, dict):
        return []
    references = automatic.get("proxies")
    if not isinstance(references, list):
        return []
    result: list[str] = []
    for name in references:
        region = region_from_display_name(str(name))
        if region is not None:
            result.append(region)
    return result


def validate_browsing_public_surface(config: dict[str, Any]) -> None:
    """Validate the provider-free public selector and regional failover graph."""

    by_name = _groups_by_name(config)
    public = by_name.get(BROWSING_PUBLIC_GROUP)
    if public is None:
        return

    errors: list[str] = []
    for name in sorted(CANONICAL_PUBLIC_GROUPS):
        group = by_name.get(name)
        if group is None:
            continue
        if bool(group.get("hidden", False)):
            errors.append(f"canonical public group {name!r} must not be hidden")
        if group.get("type") != "select":
            errors.append(f"canonical public group {name!r} must be a select group")
        if group.get("use"):
            errors.append(f"canonical public group {name!r} must not expose proxy providers")
        if "filter" in group:
            errors.append(f"canonical public group {name!r} must not contain a provider filter")

    automatic = by_name.get(BROWSING_AUTO_GROUP)
    regions = _runtime_regions(config)
    region_groups = [region_display_name(region) for region in regions]
    expected_public = [BROWSING_AUTO_GROUP, *region_groups, "DIRECT"]
    if public.get("proxies") != expected_public:
        errors.append("网页浏览 must expose 网页自动, available regional choices, then DIRECT")
    if any(str(name).startswith("[BROWSING:") for name in public.get("proxies", [])):
        errors.append("网页浏览 must never expose raw browsing runtime nodes")

    if not isinstance(automatic, dict):
        errors.append("网页自动 runtime group is missing")
    else:
        if automatic.get("type") != "fallback" or not bool(automatic.get("hidden", False)):
            errors.append("网页自动 must be a hidden regional fallback group")
        if automatic.get("use"):
            errors.append("网页自动 must not expose providers directly")
        if not regions or automatic.get("proxies") != region_groups:
            errors.append("网页自动 must contain only ordered regional browsing groups")
        if not str(automatic.get("url", "")).startswith("https://"):
            errors.append("网页自动 runtime probe must use HTTPS")

    providers = config.get("proxy-providers", {})
    if not isinstance(providers, dict):
        errors.append("browsing runtime requires proxy-providers")
        providers = {}
    for region in regions:
        region_name = region_display_name(region)
        stable_name = region_stable_group(region)
        reserve_name = region_reserve_group(region)
        region_group = by_name.get(region_name)
        if not isinstance(region_group, dict):
            errors.append(f"regional browsing group {region_name!r} is missing")
        else:
            if region_group.get("type") != "fallback" or not bool(
                region_group.get("hidden", False)
            ):
                errors.append(f"regional browsing group {region_name!r} must be a hidden fallback")
            if region_group.get("proxies") != [stable_name, reserve_name]:
                errors.append(
                    f"regional browsing group {region_name!r} must stay inside its region"
                )
            if region_group.get("use"):
                errors.append(f"regional browsing group {region_name!r} must not expose providers")

        expected_provider: str | None = None
        for name in providers:
            if provider_region(str(name)) == region:
                if expected_provider is not None:
                    errors.append(f"multiple browsing providers exist for region {region}")
                    break
                expected_provider = str(name)
        if expected_provider is None:
            errors.append(f"regional browsing provider for {region} is missing")
            continue
        for tier_name in (stable_name, reserve_name):
            tier = by_name.get(tier_name)
            if not isinstance(tier, dict):
                errors.append(f"internal browsing group {tier_name!r} is missing")
                continue
            if tier.get("type") != "url-test" or not bool(tier.get("hidden", False)):
                errors.append(f"internal browsing group {tier_name!r} must be a hidden url-test")
            if tier.get("use") != [expected_provider]:
                errors.append(f"internal browsing group {tier_name!r} must use only {region}")
            if not str(tier.get("url", "")).startswith("https://"):
                errors.append(f"internal browsing group {tier_name!r} runtime probe must use HTTPS")

    if errors:
        raise ValidationError("browsing public surface is invalid: " + "; ".join(errors))


def _comment_header(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def _region_provider_payloads(
    config: dict[str, Any],
) -> dict[str, tuple[str, list[dict[str, Any]]]]:
    providers = config.get("proxy-providers")
    if not isinstance(providers, dict):
        raise ValidationError("browsing qualification requires proxy-providers")
    result: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for provider_name in sorted(providers):
        region = provider_region(str(provider_name))
        if region is None:
            continue
        provider = providers[provider_name]
        payload = provider.get("payload") if isinstance(provider, dict) else None
        if not isinstance(payload, list):
            raise ValidationError("browsing provider payload is invalid")
        result[region] = (str(provider_name), payload)
    if not result:
        raise ValidationError("candidate contains no browsing providers")
    return result


def _remove_region_runtime(config: dict[str, Any], region: str) -> None:
    groups = config.get("proxy-groups")
    if not isinstance(groups, list):
        raise ValidationError("browsing qualification requires proxy-groups")
    remove_names = {
        region_display_name(region),
        region_stable_group(region),
        region_reserve_group(region),
    }
    groups[:] = [
        group
        for group in groups
        if not (
            isinstance(group, dict)
            and isinstance(group.get("name"), str)
            and group["name"] in remove_names
        )
    ]


def _refresh_regional_routes(config: dict[str, Any], available_regions: list[str]) -> None:
    by_name = _groups_by_name(config)
    automatic = by_name.get(BROWSING_AUTO_GROUP)
    public = by_name.get(BROWSING_PUBLIC_GROUP)
    if not isinstance(automatic, dict) or not isinstance(public, dict):
        raise ValidationError("hardened browsing route groups are missing")
    region_groups = [region_display_name(region) for region in available_regions]
    if not region_groups:
        raise ValidationError("no browsing region retained a qualified node")
    automatic["proxies"] = region_groups
    public["proxies"] = [BROWSING_AUTO_GROUP, *region_groups, "DIRECT"]


def _set_region_tier_filters(
    config: dict[str, Any], *, region: str, stable_names: set[str], reserve_names: set[str]
) -> int:
    by_name = _groups_by_name(config)
    stable_group = by_name.get(region_stable_group(region))
    reserve_group = by_name.get(region_reserve_group(region))
    if not isinstance(stable_group, dict) or not isinstance(reserve_group, dict):
        raise ValidationError(f"hardened browsing tier groups are missing for {region}")
    stable_group["filter"] = _exact_filter(stable_names)
    reserve_group["filter"] = _exact_filter(reserve_names)
    return 2


def rewrite_hardened_browsing_qualified_candidate(
    candidate_path: Path,
    qualified_names: set[str],
    stable_names: set[str],
) -> dict[str, Any]:
    """Prune failed nodes and split every surviving region into Stable/Reserve tiers."""

    try:
        original = candidate_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError("failed to read candidate for browsing runtime hardening") from exc
    config = load_yaml_file(candidate_path)
    if not isinstance(config, dict):
        raise ValidationError("candidate is not a YAML mapping")

    region_order = _runtime_regions(config)
    providers = config.get("proxy-providers")
    if not isinstance(providers, dict):
        raise ValidationError("browsing qualification requires proxy-providers")
    payloads = _region_provider_payloads(config)
    tested = 0
    available_regions: list[str] = []
    removed_regions: list[str] = []
    region_report: dict[str, dict[str, int]] = {}
    stable_automatic_nodes = 0
    reserve_automatic_nodes = 0

    for region in region_order:
        entry = payloads.get(region)
        if entry is None:
            continue
        provider_name, payload = entry
        tested += len(payload)
        kept = [
            proxy
            for proxy in payload
            if isinstance(proxy, dict) and str(proxy.get("name", "")) in qualified_names
        ]
        if not kept:
            providers.pop(provider_name, None)
            _remove_region_runtime(config, region)
            removed_regions.append(region)
            region_report[region] = {
                "tested": len(payload),
                "qualified": 0,
                "stable": 0,
                "reserve": 0,
                "stable_automatic": 0,
                "reserve_automatic": 0,
            }
            continue

        providers[provider_name]["payload"] = kept
        qualified_region = {
            str(proxy["name"])
            for proxy in kept
            if isinstance(proxy, dict) and isinstance(proxy.get("name"), str)
        }
        stable_region = stable_names & qualified_region
        effective_stable = set(stable_region) if stable_region else set(qualified_region)
        reserve_region = qualified_region - effective_stable
        if not reserve_region:
            reserve_region = set(qualified_region)
        _set_region_tier_filters(
            config,
            region=region,
            stable_names=effective_stable,
            reserve_names=reserve_region,
        )
        available_regions.append(region)
        stable_automatic_nodes += len(effective_stable)
        reserve_automatic_nodes += len(reserve_region)
        region_report[region] = {
            "tested": len(payload),
            "qualified": len(qualified_region),
            "stable": len(stable_region),
            "reserve": len(qualified_region - stable_region),
            "stable_automatic": len(effective_stable),
            "reserve_automatic": len(reserve_region),
        }

    _refresh_regional_routes(config, available_regions)
    validate_browsing_public_surface(config)
    from .validator import validate_generated_config

    validate_generated_config(config)
    atomic_write(candidate_path, _comment_header(original) + dump_yaml(config))
    qualified_total = sum(item["qualified"] for item in region_report.values())
    return {
        "tested_nodes": tested,
        "qualified_nodes": qualified_total,
        "stable_nodes": sum(item["stable"] for item in region_report.values()),
        "reserve_nodes": sum(item["reserve"] for item in region_report.values()),
        "failed_nodes": tested - qualified_total,
        "automatic_nodes": qualified_total,
        "stable_automatic_nodes": stable_automatic_nodes,
        "reserve_automatic_nodes": reserve_automatic_nodes,
        "automatic_fallback_providers": 0,
        "automatic_groups": len(available_regions) * 3 + 1,
        "automatic_failover": True,
        "regional_scheduling": True,
        "available_regions": available_regions,
        "removed_regions": removed_regions,
        "regions": region_report,
    }


def apply_browsing_history_preference(
    candidate_path: Path,
    *,
    preferred_names: set[str],
    stable_names: set[str],
    qualified_names: set[str],
) -> int:
    """Demote mature outliers region-locally without removing failover eligibility."""

    try:
        original = candidate_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError("scheduler history could not read the qualified candidate") from exc
    config = load_yaml_file(candidate_path)
    if not isinstance(config, dict):
        raise ValidationError("scheduler history candidate must be a YAML mapping")

    payloads = _region_provider_payloads(config)
    rewrites = 0
    for region in _runtime_regions(config):
        entry = payloads.get(region)
        if entry is None:
            continue
        _, payload = entry
        qualified_region = {
            str(proxy["name"])
            for proxy in payload
            if isinstance(proxy, dict) and isinstance(proxy.get("name"), str)
        } & qualified_names
        stable_region = stable_names & qualified_region
        effective_preferred = preferred_names & stable_region
        if len(effective_preferred) < _MIN_PREFERRED_STABLE_NODES:
            continue
        reserve_region = qualified_region - effective_preferred
        if not reserve_region:
            reserve_region = set(qualified_region)
        rewrites += _set_region_tier_filters(
            config,
            region=region,
            stable_names=effective_preferred,
            reserve_names=reserve_region,
        )

    if rewrites:
        validate_browsing_public_surface(config)
        from .validator import validate_generated_config

        validate_generated_config(config)
        atomic_write(candidate_path, _comment_header(original) + dump_yaml(config))
    return rewrites


def filter_matches(filter_pattern: str, runtime_name: str) -> bool:
    """Small test helper used to assert exact runtime tier filters."""

    return re.fullmatch(filter_pattern, runtime_name) is not None
