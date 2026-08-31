"""Materialize ACL4SSR policy-group semantics without coupling them to FlClash layout."""

from __future__ import annotations

from typing import Any

from .errors import GenerationError
from .util import normalize_expected_status, unique


def _groups_by_name(output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    groups = output.get("proxy-groups", [])
    if not isinstance(groups, list):
        raise GenerationError("generated proxy-groups must be a list")
    return {
        str(group["name"]): group
        for group in groups
        if isinstance(group, dict) and isinstance(group.get("name"), str)
    }


def _reachable_providers(
    start: str,
    *,
    groups: dict[str, dict[str, Any]],
    providers: dict[str, Any],
) -> list[str]:
    found: list[str] = []
    pending = [start]
    visited: set[str] = set()
    while pending:
        name = pending.pop(0)
        if name in visited:
            continue
        visited.add(name)
        group = groups.get(name)
        if not isinstance(group, dict):
            continue
        uses = group.get("use", [])
        if isinstance(uses, list):
            found.extend(
                provider_name
                for provider_name in uses
                if isinstance(provider_name, str) and provider_name in providers
            )
        references = group.get("proxies", [])
        if isinstance(references, list):
            pending.extend(
                reference
                for reference in references
                if isinstance(reference, str) and reference in groups
            )
    return unique(found)


def _resolve_route_member(
    member: dict[str, Any],
    *,
    groups: dict[str, dict[str, Any]],
    pool_display_names: dict[str, str],
) -> str:
    if "builtin" in member:
        return str(member["builtin"])
    if "group" in member:
        name = str(member["group"])
        if name not in groups:
            raise GenerationError(f"deterministic route references unknown group {name!r}")
        return name
    if "auto_pool" in member:
        pool_id = str(member["auto_pool"])
        name = pool_display_names.get(pool_id)
        if name is None or name not in groups:
            raise GenerationError(f"deterministic route references unknown pool {pool_id!r}")
        return name
    raise GenerationError("deterministic route contains an invalid member")


def _apply_test_fields(group: dict[str, Any], spec: dict[str, Any]) -> None:
    for key in ("url", "interval"):
        if key not in spec:
            raise GenerationError(f"ACL4SSR automatic group {group['name']!r} is missing {key!r}")
        group[key] = spec[key]
    if "tolerance" in spec:
        group["tolerance"] = spec["tolerance"]
    for source_key, target_key in (
        ("timeout", "timeout"),
        ("lazy", "lazy"),
        ("expected_status", "expected-status"),
    ):
        if source_key in spec:
            value = spec[source_key]
            group[target_key] = (
                normalize_expected_status(value) if source_key == "expected_status" else value
            )


def apply_acl4ssr_group_semantics(
    output: dict[str, Any],
    *,
    group_specs: list[dict[str, Any]],
    pool_specs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply provider-backed helpers, deterministic routes, and UI visibility.

    Routing Model V2 separates user selectors from internal rule targets.  A
    hidden group declaring ``route.deterministic: true`` is rewritten to a
    single next hop, so persisted Mihomo ``select`` state cannot silently alter
    application routing.  Hidden url-test/fallback groups remain legitimate
    automatic schedulers, while explicitly exposed selectors continue to be
    user-controlled.
    """

    providers = output.get("proxy-providers", {})
    if not isinstance(providers, dict):
        raise GenerationError("generated proxy-providers must be a mapping")
    groups = _groups_by_name(output)
    pool_display_names = {str(pool["id"]): str(pool["display_name"]) for pool in pool_specs}

    hidden_inventories: list[str] = []
    for pool in pool_specs:
        display_name = str(pool["display_name"])
        if not display_name.startswith("__CR_"):
            continue
        group = groups.get(display_name)
        if group is None:
            continue
        group["hidden"] = True
        hidden_inventories.append(display_name)

    provider_backed: list[str] = []
    hidden_groups: list[str] = []
    deterministic_routes: list[str] = []
    automatic_routes: list[str] = []
    for spec in group_specs:
        display_name = str(spec["display_name"])
        group = groups.get(display_name)
        if group is None:
            raise GenerationError(f"ACL4SSR group {display_name!r} was not generated")

        hidden = bool(spec.get("hidden", False))
        if hidden:
            group["hidden"] = True
            hidden_groups.append(display_name)

        route = spec.get("route")
        if isinstance(route, dict) and bool(route.get("deterministic", False)):
            if not hidden:
                raise GenerationError(
                    f"deterministic routing target {display_name!r} must be hidden"
                )
            member = route.get("member")
            if not isinstance(member, dict):
                raise GenerationError(
                    f"deterministic routing target {display_name!r} has no route member"
                )
            resolved = _resolve_route_member(
                member,
                groups=groups,
                pool_display_names=pool_display_names,
            )
            group.clear()
            group.update(
                {
                    "name": display_name,
                    "type": "select",
                    "hidden": True,
                    "proxies": [resolved],
                }
            )
            deterministic_routes.append(display_name)
            continue

        group_type = str(spec.get("type", "select"))
        group["type"] = group_type
        provider_pool = spec.get("provider_pool")
        if provider_pool is not None:
            pool_id = str(provider_pool)
            pool_display = pool_display_names.get(pool_id)
            if pool_display is None:
                raise GenerationError(
                    f"ACL4SSR group {display_name!r} references unknown provider pool {pool_id!r}"
                )
            provider_names = _reachable_providers(
                pool_display,
                groups=groups,
                providers=providers,
            )
            if not provider_names:
                raise GenerationError(
                    f"ACL4SSR group {display_name!r} could not resolve providers from pool {pool_id!r}"
                )
            group["use"] = provider_names
            provider_backed.append(display_name)

            filter_pattern = spec.get("filter")
            if isinstance(filter_pattern, str) and filter_pattern:
                group["filter"] = filter_pattern

            if not spec.get("members"):
                group.pop("proxies", None)

        if group_type == "url-test":
            if provider_pool is None:
                raise GenerationError(
                    f"ACL4SSR url-test group {display_name!r} requires provider_pool"
                )
            if "tolerance" not in spec:
                raise GenerationError(
                    f"ACL4SSR url-test group {display_name!r} is missing 'tolerance'"
                )
            _apply_test_fields(group, spec)
            automatic_routes.append(display_name)
        elif group_type == "fallback":
            references = group.get("proxies", [])
            if not isinstance(references, list) or not references:
                raise GenerationError(f"ACL4SSR fallback group {display_name!r} has no members")
            _apply_test_fields(group, spec)
            automatic_routes.append(display_name)
        elif group_type != "select":
            raise GenerationError(
                f"ACL4SSR group {display_name!r} uses unsupported runtime type {group_type!r}"
            )

    return {
        "provider_backed_groups": sorted(provider_backed),
        "hidden_groups": sorted(hidden_groups),
        "hidden_inventories": sorted(hidden_inventories),
        "deterministic_routes": sorted(deterministic_routes),
        "automatic_routes": sorted(automatic_routes),
    }
