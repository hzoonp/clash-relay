"""Production-only policy audit and safe aggregate reporting."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .config_loader import ProjectDefinition
from .errors import ValidationError
from .runtime_names import canonical_source_id
from .util import safe_identifier

_RUNTIME_SOURCE = re.compile(r"^\[[^\]]+\]\s+([^/]+)/")
_BUILTINS = frozenset({"DIRECT", "REJECT", "PASS", "COMPATIBLE"})
_DEFAULT_SOURCE_USE = "general"


def _provider_name(pool_id: str, region: str) -> str:
    return f"cr_{safe_identifier(pool_id)}_{safe_identifier(region)}"


def _runtime_source_id(
    proxy: dict[str, Any], *, provider_name: str, known_source_ids: set[str]
) -> str:
    name = proxy.get("name")
    if not isinstance(name, str):
        raise ValidationError(
            f"production audit found a proxy without a runtime name in provider {provider_name!r}"
        )
    match = _RUNTIME_SOURCE.match(name)
    if match is None:
        raise ValidationError(
            f"production audit could not recover source identity in provider {provider_name!r}"
        )
    try:
        return canonical_source_id(match.group(1), known_source_ids)
    except ValueError as exc:
        raise ValidationError("production audit found ambiguous runtime source labels") from exc


def _groups_by_name(candidate: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = candidate.get("proxy-groups")
    if not isinstance(rows, list):
        raise ValidationError("production reachability audit requires proxy-groups")
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise ValidationError("production reachability audit found a malformed proxy group")
        name = str(row["name"])
        if name in groups:
            raise ValidationError(f"production reachability audit found duplicate group {name!r}")
        groups[name] = row
    return groups


def _provider_graph(
    providers: dict[str, Any],
    *,
    known_source_ids: set[str],
) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, str]]:
    provider_sources: dict[str, set[str]] = {}
    provider_dialers: dict[str, set[str]] = {}
    runtime_sources: dict[str, str] = {}

    for provider_name, provider in providers.items():
        if not isinstance(provider_name, str) or not isinstance(provider, dict):
            raise ValidationError("production reachability audit found a malformed provider")
        payload = provider.get("payload")
        if not isinstance(payload, list):
            raise ValidationError(
                f"production reachability audit found provider {provider_name!r} without payload"
            )
        sources: set[str] = set()
        dialers: set[str] = set()
        for proxy in payload:
            if not isinstance(proxy, dict):
                raise ValidationError(
                    f"production reachability audit found malformed proxy in {provider_name!r}"
                )
            source_id = _runtime_source_id(
                proxy, provider_name=provider_name, known_source_ids=known_source_ids
            )
            sources.add(source_id)
            runtime_name = str(proxy["name"])
            previous = runtime_sources.get(runtime_name)
            if previous is not None and previous != source_id:
                raise ValidationError(
                    "runtime proxy name resolves to multiple subscription sources"
                )
            runtime_sources[runtime_name] = source_id
            dialer = proxy.get("dialer-proxy")
            if isinstance(dialer, str) and dialer:
                dialers.add(dialer)
        provider_sources[provider_name] = sources
        provider_dialers[provider_name] = dialers
    return provider_sources, provider_dialers, runtime_sources


def _reachable_sources(
    target: str,
    *,
    groups: dict[str, dict[str, Any]],
    provider_sources: dict[str, set[str]],
    provider_dialers: dict[str, set[str]],
    runtime_sources: dict[str, str],
    memo: dict[str, frozenset[str]],
    visiting: set[str] | None = None,
) -> frozenset[str]:
    if target in _BUILTINS:
        return frozenset()
    runtime_source = runtime_sources.get(target)
    if runtime_source is not None:
        return frozenset({runtime_source})
    cached = memo.get(target)
    if cached is not None:
        return cached

    group = groups.get(target)
    if group is None:
        return frozenset()
    active = set() if visiting is None else set(visiting)
    if target in active:
        raise ValidationError(f"production reachability audit found a cycle at {target!r}")
    active.add(target)

    found: set[str] = set()
    uses = group.get("use", [])
    if isinstance(uses, list):
        for provider_name in uses:
            if not isinstance(provider_name, str):
                continue
            found.update(provider_sources.get(provider_name, set()))
            for dialer in provider_dialers.get(provider_name, set()):
                found.update(
                    _reachable_sources(
                        dialer,
                        groups=groups,
                        provider_sources=provider_sources,
                        provider_dialers=provider_dialers,
                        runtime_sources=runtime_sources,
                        memo=memo,
                        visiting=active,
                    )
                )

    references = group.get("proxies", [])
    if isinstance(references, list):
        for reference in references:
            if not isinstance(reference, str):
                continue
            found.update(
                _reachable_sources(
                    reference,
                    groups=groups,
                    provider_sources=provider_sources,
                    provider_dialers=provider_dialers,
                    runtime_sources=runtime_sources,
                    memo=memo,
                    visiting=active,
                )
            )

    result = frozenset(found)
    memo[target] = result
    return result


def _assert_use_allowed(
    source_ids: frozenset[str] | set[str],
    source_use: str,
    *,
    subscriptions: dict[str, Any],
    surface: str,
) -> None:
    for source_id in sorted(source_ids):
        spec = subscriptions.get(source_id)
        if spec is None:
            raise ValidationError(
                f"production reachability audit found unknown subscription source {source_id!r}"
            )
        if "*" in spec.allowed_uses or source_use in spec.allowed_uses:
            continue
        raise ValidationError(
            "production routing reachability boundary violated: "
            f"surface {surface!r} for use {source_use!r} can reach source {source_id!r}"
        )


def _expected_group_uses(project: ProjectDefinition) -> dict[str, str]:
    modules = project.config["modules"]
    pool_uses = {
        str(pool["id"]): str(pool["source_use"])
        for pool in project.policies["pools"]
        if modules.get(str(pool["module"]), False)
    }
    expected = {
        str(pool["display_name"]): str(pool["source_use"])
        for pool in project.policies["pools"]
        if modules.get(str(pool["module"]), False)
    }
    expected.update(
        {
            str(service["display_name"]): str(service["source_use"])
            for service in project.services["services"]
            if modules.get(str(service["module"]), False)
        }
    )

    if project.acl4ssr is not None:
        for group in project.acl4ssr.get("groups", []):
            module = group.get("module")
            if module is not None and not modules.get(str(module), False):
                continue
            provider_pool = group.get("provider_pool")
            if group.get("source_use") is not None:
                source_use = str(group["source_use"])
            elif isinstance(provider_pool, str) and provider_pool in pool_uses:
                source_use = pool_uses[provider_pool]
            else:
                source_use = _DEFAULT_SOURCE_USE
            expected[str(group["display_name"])] = source_use
    return expected


def _audit_reachability(
    project: ProjectDefinition,
    candidate: dict[str, Any],
    *,
    subscriptions: dict[str, Any],
    providers: dict[str, Any],
) -> dict[str, Any]:
    groups = _groups_by_name(candidate)
    provider_sources, provider_dialers, runtime_sources = _provider_graph(
        providers, known_source_ids=set(subscriptions)
    )
    memo: dict[str, frozenset[str]] = {}
    use_counts: Counter[str] = Counter()
    groups_checked = 0

    for group_name, source_use in sorted(_expected_group_uses(project).items()):
        if group_name not in groups:
            continue
        reachable = _reachable_sources(
            group_name,
            groups=groups,
            provider_sources=provider_sources,
            provider_dialers=provider_dialers,
            runtime_sources=runtime_sources,
            memo=memo,
        )
        _assert_use_allowed(
            reachable,
            source_use,
            subscriptions=subscriptions,
            surface=f"group:{group_name}",
        )
        use_counts[source_use] += 1
        groups_checked += 1

    routing_surfaces_checked = 0
    runtime_rules_checked = 0
    manifest = project.acl4ssr
    if manifest is not None:
        modules = project.config["modules"]
        source_use_by_provider: dict[str, str] = {}
        for row in manifest["sources"]:
            module = row.get("module")
            if module is not None and not modules.get(str(module), False):
                continue
            source_use = str(row.get("source_use", _DEFAULT_SOURCE_USE))
            target = str(row["target"])
            reachable = _reachable_sources(
                target,
                groups=groups,
                provider_sources=provider_sources,
                provider_dialers=provider_dialers,
                runtime_sources=runtime_sources,
                memo=memo,
            )
            _assert_use_allowed(
                reachable,
                source_use,
                subscriptions=subscriptions,
                surface=f"rule-source:{row['id']}",
            )
            source_use_by_provider[f"acl4ssr_{row['id']}"] = source_use
            use_counts[source_use] += 1
            routing_surfaces_checked += 1

        for row in manifest.get("inline_rules", []):
            module = row.get("module")
            if module is not None and not modules.get(str(module), False):
                continue
            source_use = str(row.get("source_use", _DEFAULT_SOURCE_USE))
            reachable = _reachable_sources(
                str(row["target"]),
                groups=groups,
                provider_sources=provider_sources,
                provider_dialers=provider_dialers,
                runtime_sources=runtime_sources,
                memo=memo,
            )
            _assert_use_allowed(
                reachable,
                source_use,
                subscriptions=subscriptions,
                surface=f"inline-rule:{row['id']}",
            )
            use_counts[source_use] += 1
            routing_surfaces_checked += 1

        final_target = manifest.get("final_target")
        if isinstance(final_target, str):
            final_use = str(manifest.get("final_source_use", _DEFAULT_SOURCE_USE))
            reachable = _reachable_sources(
                final_target,
                groups=groups,
                provider_sources=provider_sources,
                provider_dialers=provider_dialers,
                runtime_sources=runtime_sources,
                memo=memo,
            )
            _assert_use_allowed(
                reachable,
                final_use,
                subscriptions=subscriptions,
                surface="final-target",
            )
            use_counts[final_use] += 1
            routing_surfaces_checked += 1

        rules = candidate.get("rules", [])
        if not isinstance(rules, list):
            raise ValidationError("production reachability audit requires routing rules")
        final_use = str(manifest.get("final_source_use", _DEFAULT_SOURCE_USE))
        for raw_rule in rules:
            if not isinstance(raw_rule, str):
                continue
            parts = raw_rule.split(",")
            source_use: str | None = None
            target: str | None = None
            if len(parts) >= 3 and parts[0] == "RULE-SET":
                provider_name = parts[1]
                source_use = source_use_by_provider.get(provider_name)
                if source_use is None and provider_name.startswith("cr_ai_rules_"):
                    source_use = "ai"
                target = parts[2]
            elif len(parts) >= 2 and parts[0] == "MATCH":
                source_use = final_use
                target = parts[1]
            if source_use is None or target is None:
                continue
            reachable = _reachable_sources(
                target,
                groups=groups,
                provider_sources=provider_sources,
                provider_dialers=provider_dialers,
                runtime_sources=runtime_sources,
                memo=memo,
            )
            _assert_use_allowed(
                reachable,
                source_use,
                subscriptions=subscriptions,
                surface=f"runtime-rule:{parts[0]}:{parts[1]}",
            )
            runtime_rules_checked += 1

    return {
        "status": "passed",
        "groups_checked": groups_checked,
        "routing_surfaces_checked": routing_surfaces_checked,
        "runtime_rules_checked": runtime_rules_checked,
        "source_use_checks": dict(sorted(use_counts.items())),
    }


def audit_production_candidate(
    project: ProjectDefinition,
    candidate: dict[str, Any],
    *,
    build_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify provider membership and end-to-end routing reachability boundaries.

    The audit intentionally exposes only subscription IDs and aggregate counts.
    It never returns node names, servers, ports, credentials, or subscription URLs.
    """

    providers = candidate.get("proxy-providers")
    if not isinstance(providers, dict):
        raise ValidationError("production audit requires proxy-providers")

    subscriptions = {item.id: item for item in project.subscriptions if item.enabled}
    pool_rows: list[dict[str, Any]] = []

    for pool in project.policies["pools"]:
        if not project.config["modules"].get(pool["module"], False):
            continue

        source_use = str(pool["source_use"])
        counts: Counter[str] = Counter()
        provider_count = 0

        for region in pool["regions"]:
            provider_name = _provider_name(str(pool["id"]), str(region))
            provider = providers.get(provider_name)
            if provider is None:
                continue
            if not isinstance(provider, dict):
                raise ValidationError(
                    f"production audit found malformed provider {provider_name!r}"
                )
            payload = provider.get("payload")
            if not isinstance(payload, list):
                raise ValidationError(
                    f"production audit found provider {provider_name!r} without inline payload"
                )
            provider_count += 1
            for proxy in payload:
                if not isinstance(proxy, dict):
                    raise ValidationError(
                        f"production audit found malformed proxy in provider {provider_name!r}"
                    )
                source_id = _runtime_source_id(
                    proxy, provider_name=provider_name, known_source_ids=set(subscriptions)
                )
                spec = subscriptions.get(source_id)
                if spec is None:
                    raise ValidationError(
                        f"production audit found unknown subscription source {source_id!r}"
                    )
                if "*" not in spec.allowed_uses and source_use not in spec.allowed_uses:
                    raise ValidationError(
                        "production source-use boundary violated: "
                        f"source {source_id!r} entered pool {pool['id']!r} "
                        f"for use {source_use!r}"
                    )
                counts[source_id] += 1

        pool_rows.append(
            {
                "id": str(pool["id"]),
                "display_name": str(pool["display_name"]),
                "source_use": source_use,
                "providers": provider_count,
                "nodes": sum(counts.values()),
                "sources": dict(sorted(counts.items())),
            }
        )

    reachability = _audit_reachability(
        project,
        candidate,
        subscriptions=subscriptions,
        providers=providers,
    )

    source_reports = {}
    if build_report is not None:
        raw_reports = build_report.get("subscriptions", [])
        if not isinstance(raw_reports, list):
            raise ValidationError("production build report has invalid subscriptions summary")
        source_reports = {
            str(item.get("id")): item
            for item in raw_reports
            if isinstance(item, dict) and item.get("id")
        }

    subscription_rows: list[dict[str, Any]] = []
    for spec in sorted(subscriptions.values(), key=lambda item: (item.priority, item.id)):
        source_report = source_reports.get(spec.id, {})
        row: dict[str, Any] = {
            "id": spec.id,
            "status": str(source_report.get("status", "unknown")),
            "allowed_uses": sorted(spec.allowed_uses),
            "nodes": int(source_report.get("nodes", 0) or 0),
            "filtered_over_multiplier": int(source_report.get("filtered_over_multiplier", 0) or 0),
        }
        if spec.max_node_multiplier is not None:
            row["max_node_multiplier"] = spec.max_node_multiplier
        subscription_rows.append(row)

    return {
        "status": "passed",
        "subscriptions": subscription_rows,
        "pools": pool_rows,
        "reachability": reachability,
    }


def render_production_summary_markdown(summary: dict[str, Any]) -> str:
    """Render a GitHub Actions summary without sensitive node-level material."""

    reachability = summary["reachability"]
    lines = [
        "## clash-relay production audit",
        "",
        "Source-use policy: **passed**.",
        "Routing graph reachability: **passed**.",
        "",
        "### Subscriptions",
        "",
        "| Source | Status | Accepted nodes | > multiplier filtered | Max multiplier | Allowed uses |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for item in summary["subscriptions"]:
        maximum = item.get("max_node_multiplier", "-")
        uses = ", ".join(item["allowed_uses"])
        lines.append(
            f"| `{item['id']}` | {item['status']} | {item['nodes']} | "
            f"{item['filtered_over_multiplier']} | {maximum} | {uses} |"
        )

    lines.extend(
        [
            "",
            "### Scenario pools",
            "",
            "| Pool | Use | Nodes | Source counts |",
            "| --- | --- | ---: | --- |",
        ]
    )
    for item in summary["pools"]:
        sources = (
            ", ".join(f"`{source}`={count}" for source, count in item["sources"].items()) or "-"
        )
        lines.append(f"| `{item['id']}` | `{item['source_use']}` | {item['nodes']} | {sources} |")

    lines.extend(
        [
            "",
            "### Reachability audit",
            "",
            f"Declared groups checked: **{reachability['groups_checked']}**  ",
            f"Routing surfaces checked: **{reachability['routing_surfaces_checked']}**  ",
            f"Runtime rules checked: **{reachability['runtime_rules_checked']}**",
            "",
            "This summary intentionally omits node names, servers, ports, credentials, and subscription URLs.",
            "",
        ]
    )
    return "\n".join(lines)
