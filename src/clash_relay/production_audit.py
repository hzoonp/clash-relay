"""Production-only policy audit and safe aggregate reporting."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .config_loader import ProjectDefinition
from .errors import ValidationError
from .util import safe_identifier

_RUNTIME_SOURCE = re.compile(r"^\[[^\]]+\]\s+([^/]+)/")


def _provider_name(pool_id: str, region: str) -> str:
    return f"cr_{safe_identifier(pool_id)}_{safe_identifier(region)}"


def _runtime_source_id(proxy: dict[str, Any], *, provider_name: str) -> str:
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
    return match.group(1)


def audit_production_candidate(
    project: ProjectDefinition,
    candidate: dict[str, Any],
    *,
    build_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify runtime provider membership against declared source-use boundaries.

    The audit intentionally exposes only subscription IDs and aggregate counts.
    It never returns node names, servers, ports, credentials, or subscription URLs.
    """

    providers = candidate.get("proxy-providers")
    if not isinstance(providers, dict):
        raise ValidationError("production audit requires proxy-providers")

    subscriptions = {
        item.id: item for item in project.subscriptions if item.enabled
    }
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
                source_id = _runtime_source_id(proxy, provider_name=provider_name)
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
    for spec in sorted(
        subscriptions.values(), key=lambda item: (item.priority, item.id)
    ):
        source_report = source_reports.get(spec.id, {})
        row: dict[str, Any] = {
            "id": spec.id,
            "status": str(source_report.get("status", "unknown")),
            "allowed_uses": sorted(spec.allowed_uses),
            "nodes": int(source_report.get("nodes", 0) or 0),
            "filtered_over_multiplier": int(
                source_report.get("filtered_over_multiplier", 0) or 0
            ),
        }
        if spec.max_node_multiplier is not None:
            row["max_node_multiplier"] = spec.max_node_multiplier
        subscription_rows.append(row)

    return {
        "status": "passed",
        "subscriptions": subscription_rows,
        "pools": pool_rows,
    }


def render_production_summary_markdown(summary: dict[str, Any]) -> str:
    """Render a GitHub Actions summary without sensitive node-level material."""

    lines = [
        "## clash-relay production audit",
        "",
        "Source-use policy: **passed**.",
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
            ", ".join(
                f"`{source}`={count}" for source, count in item["sources"].items()
            )
            or "-"
        )
        lines.append(
            f"| `{item['id']}` | `{item['source_use']}` | {item['nodes']} | {sources} |"
        )

    lines.append("")
    lines.append(
        "This summary intentionally omits node names, servers, ports, credentials, and subscription URLs."
    )
    lines.append("")
    return "\n".join(lines)
