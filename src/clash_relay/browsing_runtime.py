"""Runtime hardening for the canonical browsing scenario.

The generated inventory is intentionally provider-backed, but public scenario
selectors must never expose those providers directly.  This module converts the
canonical browsing surface into a two-tier automatic scheduler:

    网页浏览 -> 网页自动 -> stable url-test -> reserve url-test
              \-> DIRECT      (fallback)

Only the first two names are user-facing/presentation names.  The stable and
reserve groups are internal implementation details.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .errors import GenerationError, ValidationError
from .util import atomic_write, dump_yaml, load_yaml_file, normalize_expected_status

BROWSING_PUBLIC_GROUP = "网页浏览"
BROWSING_AUTO_GROUP = "网页自动"
BROWSING_STABLE_GROUP = "__CR_BROWSING_STABLE_AUTO"
BROWSING_RESERVE_GROUP = "__CR_BROWSING_RESERVE_AUTO"
BROWSING_PROVIDER_PREFIX = "cr_browsing_"
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


def _browsing_probe(policies: dict[str, Any]) -> dict[str, Any]:
    pools = policies.get("pools")
    probes = policies.get("probes")
    if not isinstance(pools, list) or not isinstance(probes, dict):
        raise GenerationError("browsing runtime requires policy pools and probes")
    pool = next(
        (item for item in pools if isinstance(item, dict) and item.get("id") == "browsing"),
        None,
    )
    if not isinstance(pool, dict):
        raise GenerationError("browsing runtime requires the canonical browsing pool")
    probe_name = pool.get("probe")
    probe = probes.get(probe_name) if isinstance(probe_name, str) else None
    if not isinstance(probe, dict):
        raise GenerationError("browsing runtime probe is missing")
    url = probe.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise GenerationError("canonical browsing runtime probe must use HTTPS")
    return probe


def _runtime_test_fields(probe: dict[str, Any], *, tolerance: bool) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "url": str(probe["url"]),
        "interval": int(probe["interval"]),
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


def harden_browsing_runtime(
    config: dict[str, Any], policies: dict[str, Any]
) -> dict[str, Any]:
    """Remove provider exposure from the public browser selector and add failover tiers."""

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
    browsing_providers = [str(name) for name in uses]
    if any(
        name not in providers or not name.startswith(BROWSING_PROVIDER_PREFIX)
        for name in browsing_providers
    ):
        raise GenerationError("canonical browsing automatic group references a non-browsing provider")

    probe = _browsing_probe(policies)
    provider_fields = _runtime_test_fields(probe, tolerance=False)
    for provider_name in browsing_providers:
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

    public.clear()
    public.update(
        {
            "name": BROWSING_PUBLIC_GROUP,
            "type": "select",
            "proxies": [BROWSING_AUTO_GROUP, "DIRECT"],
        }
    )

    scheduler_fields = _runtime_test_fields(probe, tolerance=True)
    stable_group = {
        "name": BROWSING_STABLE_GROUP,
        "type": "url-test",
        "hidden": True,
        "use": list(browsing_providers),
        "filter": ".*",
        **scheduler_fields,
    }
    reserve_group = {
        "name": BROWSING_RESERVE_GROUP,
        "type": "url-test",
        "hidden": True,
        "use": list(browsing_providers),
        "filter": ".*",
        **scheduler_fields,
    }
    _replace_or_append_group(groups, by_name, BROWSING_STABLE_GROUP, stable_group)
    _replace_or_append_group(groups, by_name, BROWSING_RESERVE_GROUP, reserve_group)

    automatic.clear()
    automatic.update(
        {
            "name": BROWSING_AUTO_GROUP,
            "type": "fallback",
            "hidden": True,
            "proxies": [BROWSING_STABLE_GROUP, BROWSING_RESERVE_GROUP],
            **_runtime_test_fields(probe, tolerance=False),
        }
    )

    validate_browsing_public_surface(config)
    return {
        "status": "hardened",
        "public_group": BROWSING_PUBLIC_GROUP,
        "automatic_group": BROWSING_AUTO_GROUP,
        "stable_group": BROWSING_STABLE_GROUP,
        "reserve_group": BROWSING_RESERVE_GROUP,
        "providers": list(browsing_providers),
        "probe": str(probe["url"]),
    }


def validate_browsing_public_surface(config: dict[str, Any]) -> None:
    """Validate the canonical no-provider public surface and automatic failover contract."""

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

    if public.get("proxies") != [BROWSING_AUTO_GROUP, "DIRECT"]:
        errors.append("网页浏览 must expose only 网页自动 and DIRECT")

    automatic = by_name.get(BROWSING_AUTO_GROUP)
    if not isinstance(automatic, dict):
        errors.append("网页自动 runtime group is missing")
    else:
        if automatic.get("type") != "fallback" or not bool(automatic.get("hidden", False)):
            errors.append("网页自动 must be a hidden fallback group")
        if automatic.get("proxies") != [BROWSING_STABLE_GROUP, BROWSING_RESERVE_GROUP]:
            errors.append("网页自动 must fail over from stable to reserve browsing tiers")
        if automatic.get("use"):
            errors.append("网页自动 must not expose providers directly")
        if not str(automatic.get("url", "")).startswith("https://"):
            errors.append("网页自动 runtime probe must use HTTPS")

    providers = config.get("proxy-providers", {})
    for name in (BROWSING_STABLE_GROUP, BROWSING_RESERVE_GROUP):
        group = by_name.get(name)
        if not isinstance(group, dict):
            errors.append(f"internal browsing group {name!r} is missing")
            continue
        if group.get("type") != "url-test" or not bool(group.get("hidden", False)):
            errors.append(f"internal browsing group {name!r} must be a hidden url-test")
        uses = group.get("use")
        if not isinstance(uses, list) or not uses:
            errors.append(f"internal browsing group {name!r} has no providers")
        elif any(
            provider not in providers or not str(provider).startswith(BROWSING_PROVIDER_PREFIX)
            for provider in uses
        ):
            errors.append(f"internal browsing group {name!r} references a non-browsing provider")
        if not str(group.get("url", "")).startswith("https://"):
            errors.append(f"internal browsing group {name!r} runtime probe must use HTTPS")

    if errors:
        raise ValidationError("browsing public surface is invalid: " + "; ".join(errors))


def _comment_header(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def _qualified_provider_names(
    config: dict[str, Any], qualified_names: set[str]
) -> tuple[int, dict[str, set[str]]]:
    providers = config.get("proxy-providers")
    if not isinstance(providers, dict):
        raise ValidationError("browsing qualification requires proxy-providers")
    tested = 0
    names_by_provider: dict[str, set[str]] = {}
    found = False
    for provider_name in sorted(providers):
        if not str(provider_name).startswith(BROWSING_PROVIDER_PREFIX):
            continue
        found = True
        provider = providers[provider_name]
        payload = provider.get("payload") if isinstance(provider, dict) else None
        if not isinstance(payload, list):
            raise ValidationError("browsing provider payload is invalid")
        tested += len(payload)
        kept = [
            proxy
            for proxy in payload
            if isinstance(proxy, dict) and str(proxy.get("name", "")) in qualified_names
        ]
        if not kept:
            raise ValidationError(
                f"browsing qualification left provider {provider_name!r} empty; refusing publication"
            )
        provider["payload"] = kept
        names_by_provider[str(provider_name)] = {
            str(proxy["name"])
            for proxy in kept
            if isinstance(proxy, dict) and isinstance(proxy.get("name"), str)
        }
    if not found:
        raise ValidationError("candidate contains no browsing providers")
    return tested, names_by_provider


def _set_tier_filters(
    config: dict[str, Any], *, stable_names: set[str], reserve_names: set[str]
) -> int:
    by_name = _groups_by_name(config)
    stable_group = by_name.get(BROWSING_STABLE_GROUP)
    reserve_group = by_name.get(BROWSING_RESERVE_GROUP)
    if not isinstance(stable_group, dict) or not isinstance(reserve_group, dict):
        raise ValidationError("hardened browsing tier groups are missing")
    stable_group["filter"] = _exact_filter(stable_names)
    reserve_group["filter"] = _exact_filter(reserve_names)
    return 2


def rewrite_hardened_browsing_qualified_candidate(
    candidate_path: Path,
    qualified_names: set[str],
    stable_names: set[str],
) -> dict[str, Any]:
    """Prune failed nodes and split all qualified nodes into stable and reserve auto tiers."""

    try:
        original = candidate_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError("failed to read candidate for browsing runtime hardening") from exc
    config = load_yaml_file(candidate_path)
    if not isinstance(config, dict):
        raise ValidationError("candidate is not a YAML mapping")

    tested, _ = _qualified_provider_names(config, qualified_names)
    effective_stable = stable_names & qualified_names
    if not effective_stable:
        effective_stable = set(qualified_names)
    reserve = set(qualified_names) - effective_stable
    if not reserve:
        reserve = set(qualified_names)
    automatic_groups = _set_tier_filters(
        config,
        stable_names=effective_stable,
        reserve_names=reserve,
    )
    validate_browsing_public_surface(config)
    from .validator import validate_generated_config

    validate_generated_config(config)
    atomic_write(candidate_path, _comment_header(original) + dump_yaml(config))
    return {
        "tested_nodes": tested,
        "qualified_nodes": len(qualified_names),
        "stable_nodes": len(stable_names & qualified_names),
        "reserve_nodes": len(qualified_names - stable_names),
        "failed_nodes": tested - len(qualified_names),
        "automatic_nodes": len(qualified_names),
        "stable_automatic_nodes": len(effective_stable),
        "reserve_automatic_nodes": len(reserve),
        "automatic_fallback_providers": 0,
        "automatic_groups": automatic_groups,
        "automatic_failover": True,
    }


def apply_browsing_history_preference(
    candidate_path: Path,
    *,
    preferred_names: set[str],
    stable_names: set[str],
    qualified_names: set[str],
) -> int:
    """Demote historical outliers from stable to reserve without removing failover eligibility."""

    effective_preferred = preferred_names & stable_names & qualified_names
    if len(effective_preferred) < _MIN_PREFERRED_STABLE_NODES:
        return 0
    try:
        original = candidate_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError("scheduler history could not read the qualified candidate") from exc
    config = load_yaml_file(candidate_path)
    if not isinstance(config, dict):
        raise ValidationError("scheduler history candidate must be a YAML mapping")

    reserve = set(qualified_names) - effective_preferred
    if not reserve:
        reserve = set(qualified_names)
    rewrites = _set_tier_filters(
        config,
        stable_names=effective_preferred,
        reserve_names=reserve,
    )
    validate_browsing_public_surface(config)
    from .validator import validate_generated_config

    validate_generated_config(config)
    atomic_write(candidate_path, _comment_header(original) + dump_yaml(config))
    return rewrites


def filter_matches(filter_pattern: str, runtime_name: str) -> bool:
    """Small test helper used to assert exact runtime tier filters."""

    return re.fullmatch(filter_pattern, runtime_name) is not None
