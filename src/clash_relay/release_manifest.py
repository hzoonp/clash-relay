"""Privacy-safe aggregate manifest for one production candidate/release."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from . import __version__
from .errors import ValidationError
from .runtime_graph import RuntimeGraph

PUBLIC_CONFIG_VERSION = 2


def _source_inventory(audit: dict[str, Any]) -> dict[str, Any]:
    subscriptions = audit.get("subscriptions", [])
    if not isinstance(subscriptions, list):
        subscriptions = []
    configured = 0
    active = 0
    accepted_nodes = 0
    filtered_nodes = 0
    for row in subscriptions:
        if not isinstance(row, dict):
            continue
        configured += 1
        if row.get("status") == "ok":
            active += 1
        accepted_nodes += int(row.get("nodes", 0) or 0)
        filtered_nodes += int(row.get("filtered_over_multiplier", 0) or 0)

    aggregate: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"pools": 0, "providers": 0, "node_entries": 0, "sources": set()}
    )
    pools = audit.get("pools", [])
    if not isinstance(pools, list):
        pools = []
    for row in pools:
        if not isinstance(row, dict):
            continue
        source_use = str(row.get("source_use", "unknown"))
        target = aggregate[source_use]
        target["pools"] += 1
        target["providers"] += int(row.get("providers", 0) or 0)
        target["node_entries"] += int(row.get("nodes", 0) or 0)
        sources = row.get("sources", {})
        if isinstance(sources, dict):
            target["sources"].update(str(name) for name in sources)

    per_use = {
        source_use: {
            "pools": int(values["pools"]),
            "providers": int(values["providers"]),
            "node_entries": int(values["node_entries"]),
            "distinct_sources": len(values["sources"]),
        }
        for source_use, values in sorted(aggregate.items())
    }
    return {
        "configured": configured,
        "active": active,
        "accepted_nodes": accepted_nodes,
        "filtered_over_multiplier": filtered_nodes,
        "by_use": per_use,
    }


def build_release_manifest(
    *,
    candidate: dict[str, Any],
    audit: dict[str, Any],
    qualification: dict[str, Any],
    promotion_guard: dict[str, Any] | None,
    matrix: dict[str, Any],
    release: dict[str, Any] | None,
    publication_status: str,
    policy_model_version: int,
    candidate_bytes: bytes | None = None,
    commit_sha: str | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a machine-readable manifest that cannot disclose node-level material."""

    if publication_status not in {"dry-run", "published"}:
        raise ValidationError("release manifest publication_status is invalid")
    if candidate_bytes is None:
        raise ValidationError("release manifest requires exact candidate bytes")
    candidate_sha = hashlib.sha256(candidate_bytes).hexdigest()
    candidate_size = len(candidate_bytes)
    if candidate_size == 0:
        raise ValidationError("release manifest refuses an empty candidate")

    if release is not None:
        release_sha = str(release.get("sha256", ""))
        release_bytes = int(release.get("bytes", -1) or -1)
        if release_sha != candidate_sha or release_bytes != candidate_size:
            raise ValidationError("release manifest identity does not match exact candidate bytes")
        release_id = str(release.get("release_id", ""))
        if release_id != candidate_sha:
            raise ValidationError("release manifest release id does not match candidate SHA-256")
    else:
        release_id = candidate_sha

    graph = RuntimeGraph.from_candidate(candidate)
    promotion = promotion_guard if isinstance(promotion_guard, dict) else {}
    violations = promotion.get("violations", [])
    if not isinstance(violations, list):
        violations = []
    validated_cores = matrix.get("validated_cores", [])
    if not isinstance(validated_cores, list):
        validated_cores = []

    timestamp = generated_at or datetime.now(UTC)
    document: dict[str, Any] = {
        "schema_version": 1,
        "project_version": __version__,
        "public_config_version": PUBLIC_CONFIG_VERSION,
        "publication_status": publication_status,
        "release_id": release_id,
        "config_sha256": candidate_sha,
        "config_bytes": candidate_size,
        "policy_model_version": int(policy_model_version),
        "generated_at": timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "runtime": {
            "groups": len(graph.groups),
            "providers": len(graph.providers),
            "unique_nodes": len(graph.proxies),
        },
        "sources": _source_inventory(audit),
        "qualification": {
            "status": str(qualification.get("status", "unknown")),
            "policy_model_version": int(
                qualification.get("policy_model_version", policy_model_version)
                or policy_model_version
            ),
        },
        "promotion_guard": {
            "status": str(promotion.get("status", "not_applicable")),
            "reason": str(promotion.get("reason", "not_applicable")),
            "violations": len(violations),
        },
        "mihomo": {
            "status": str(matrix.get("status", "unknown")),
            "channel": str(matrix.get("channel", "unknown")),
            "validated_cores": [str(item) for item in validated_cores],
        },
    }
    if commit_sha:
        document["commit_sha"] = commit_sha
    if release is not None:
        document["release_status"] = str(release.get("status", "unknown"))
        document["production_changed"] = bool(release.get("production_changed", False))
        previous = release.get("previous_release_id")
        if isinstance(previous, str) and previous:
            document["previous_release_id"] = previous
    return document


def render_release_manifest_markdown(manifest: dict[str, Any]) -> str:
    """Render the safe manifest as a compact GitHub Actions summary."""

    runtime = manifest.get("runtime", {})
    sources = manifest.get("sources", {})
    promotion = manifest.get("promotion_guard", {})
    mihomo = manifest.get("mihomo", {})
    cores = mihomo.get("validated_cores", []) if isinstance(mihomo, dict) else []
    core_text = ", ".join(str(item) for item in cores) if cores else "-"
    lines = [
        "## Release manifest",
        "",
        f"Project: **v{manifest.get('project_version', 'unknown')}**  ",
        f"Public Config: **v{int(manifest.get('public_config_version', 0) or 0)}**  ",
        f"Publication: **{manifest.get('publication_status', 'unknown')}**  ",
        f"Release: `{manifest.get('release_id', 'unknown')}`  ",
        f"Config SHA-256: `{manifest.get('config_sha256', 'unknown')}`  ",
        f"Config bytes: **{int(manifest.get('config_bytes', 0) or 0)}**  ",
        f"Policy Model: **v{int(manifest.get('policy_model_version', 0) or 0)}**",
        "",
        f"Runtime: **{int(runtime.get('groups', 0) or 0)} groups / {int(runtime.get('providers', 0) or 0)} providers / {int(runtime.get('unique_nodes', 0) or 0)} unique nodes**  ",
        f"Sources: **{int(sources.get('active', 0) or 0)}/{int(sources.get('configured', 0) or 0)} active**  ",
        f"Promotion Guard: **{promotion.get('status', 'not_applicable')}**  ",
        f"Mihomo cores: **{core_text}**",
        "",
        "This manifest is aggregate-only and excludes node names, servers, ports, credentials, subscription URLs, and probe endpoints.",
        "",
    ]
    return "\n".join(lines)
