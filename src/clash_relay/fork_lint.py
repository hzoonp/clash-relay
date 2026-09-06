"""Privacy-safe fork configuration lint for doctor output."""

from __future__ import annotations

from typing import Any


def build_fork_lint(project: Any) -> dict[str, Any]:
    """Summarize public fork policy boundaries without reading secret values."""

    enabled = [spec for spec in project.subscriptions if spec.enabled]
    by_use = {
        use: sum(1 for spec in enabled if use in spec.allowed_uses)
        for use in ("general", "browsing", "ai")
    }
    warnings: list[str] = []
    if not enabled:
        warnings.append("no enabled subscriptions")
    for use, count in by_use.items():
        if count == 0:
            warnings.append(f"no enabled subscription admits {use} use")

    sources = [
        {
            "id": spec.id,
            "required": bool(spec.required),
            "secret_name": spec.secret_name,
            "ingest_order": int(spec.priority),
            "on_error": spec.on_error,
            "allowed_uses": sorted(spec.allowed_uses),
            "allowed_country_count": len(spec.allowed_countries),
            "max_node_multiplier": spec.max_node_multiplier,
            "deny_name_pattern_count": len(spec.deny_name_patterns),
        }
        for spec in enabled
    ]

    return {
        "status": "passed" if not warnings else "warning",
        "warnings": warnings,
        "enabled_sources": len(enabled),
        "sources_by_use": by_use,
        "restricted_non_general_sources": sum(
            1 for spec in enabled if "general" not in spec.allowed_uses
        ),
        "multiplier_capped_sources": sum(
            1 for spec in enabled if spec.max_node_multiplier is not None
        ),
        "deny_filtered_sources": sum(1 for spec in enabled if spec.deny_name_patterns),
        "sources": sources,
        "secrets": {
            "status": "not_checked",
            "expected_names": sorted(spec.secret_name for spec in enabled),
        },
        "dry_run": {
            "publication_default": False,
            "recommended_sequence": [
                "clash-relay doctor --public-only",
                "clash-relay doctor",
                "GitHub Actions workflow_dispatch with publish=false",
            ],
        },
    }
