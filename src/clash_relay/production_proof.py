"""Build a privacy-safe proof for a validated production candidate."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .util import load_yaml_file

_ALLOWED_PUBLICATION_STATUSES = frozenset({"dry-run", "published"})


def _json_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"production proof requires a {label} mapping")
    return value


def build_production_proof(
    *,
    candidate_path: Path,
    audit: dict[str, Any],
    browsing: dict[str, Any],
    ai: dict[str, Any],
    validated_cores: tuple[str, ...],
    publication_status: str,
) -> dict[str, Any]:
    """Return aggregate-only metadata for the exact validated candidate bytes."""
    if publication_status not in _ALLOWED_PUBLICATION_STATUSES:
        raise ValidationError("production proof received an invalid publication status")
    if not validated_cores or len(set(validated_cores)) != len(validated_cores):
        raise ValidationError("production proof requires unique validated Mihomo core versions")
    try:
        content = candidate_path.read_bytes()
    except OSError as exc:
        raise ValidationError("production proof could not read the private candidate") from exc
    if not content:
        raise ValidationError("production proof refuses an empty candidate")
    candidate = load_yaml_file(candidate_path)
    if not isinstance(candidate, dict):
        raise ValidationError("production proof candidate must be a YAML mapping")

    audit = _json_mapping(audit, "post-qualification audit")
    reachability = _json_mapping(audit.get("reachability"), "reachability audit")
    if audit.get("status") != "passed" or reachability.get("status") != "passed":
        raise ValidationError("production proof requires a passed source reachability audit")

    browsing = _json_mapping(browsing, "browsing qualification")
    browsing_diagnostics = _json_mapping(
        browsing.get("diagnostics"), "browsing qualification diagnostics"
    )
    if browsing.get("status") != "qualified":
        raise ValidationError("production proof requires successful browsing qualification")

    ai = _json_mapping(ai, "AI qualification")
    ai_diagnostics = _json_mapping(ai.get("diagnostics"), "AI qualification diagnostics")
    service_counts = _json_mapping(ai.get("service_qualified_nodes"), "AI service counts")
    if ai.get("status") != "qualified":
        raise ValidationError("production proof requires successful AI qualification")

    return {
        "status": "passed",
        "candidate": {
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "proxy_providers": len(candidate.get("proxy-providers", {})),
            "proxy_groups": len(candidate.get("proxy-groups", [])),
            "rule_providers": len(candidate.get("rule-providers", {})),
            "rules": len(candidate.get("rules", [])),
        },
        "source_reachability": {
            "status": "passed",
            "groups_checked": int(reachability.get("groups_checked", 0)),
            "routing_surfaces_checked": int(reachability.get("routing_surfaces_checked", 0)),
            "runtime_rules_checked": int(reachability.get("runtime_rules_checked", 0)),
        },
        "browsing": {
            "tested": int(browsing_diagnostics.get("tested_nodes", 0)),
            "qualified": int(browsing.get("qualified_nodes", 0)),
            "stable": int(browsing.get("stable_nodes", 0)),
            "reserve": int(browsing.get("reserve_nodes", 0)),
            "rejected": int(browsing.get("failed_nodes", 0)),
            "automatic": int(browsing.get("automatic_nodes", 0)),
        },
        "ai": {
            "tested": int(ai_diagnostics.get("tested_nodes", 0)),
            "selector_failures": int(ai_diagnostics.get("selector_failures", 0)),
            "service_qualified": {
                str(name): int(count) for name, count in sorted(service_counts.items())
            },
            "service_fail_closed": sorted(str(name) for name in ai.get("service_fail_closed", [])),
        },
        "validated_cores": list(validated_cores),
        "publication": publication_status,
    }


def render_production_proof_markdown(proof: dict[str, Any]) -> str:
    """Render the safe proof for GitHub Actions Summary."""
    candidate = _json_mapping(proof.get("candidate"), "candidate proof")
    reachability = _json_mapping(proof.get("source_reachability"), "reachability proof")
    browsing = _json_mapping(proof.get("browsing"), "browsing proof")
    ai = _json_mapping(proof.get("ai"), "AI proof")
    service_counts = _json_mapping(ai.get("service_qualified"), "AI service proof")
    cores = proof.get("validated_cores")
    if not isinstance(cores, list):
        raise ValidationError("production proof has invalid core metadata")

    lines = [
        "## Production proof",
        "",
        f"Publication: **{proof.get('publication')}**  ",
        f"Candidate: **{candidate['bytes']} bytes** · SHA-256 `{candidate['sha256']}`  ",
        f"Validated cores: **{', '.join(str(item) for item in cores)}**",
        "",
        "| Gate | Result |",
        "| --- | ---: |",
        f"| Source reachability | {reachability['status']} |",
        f"| Routing groups checked | {reachability['groups_checked']} |",
        f"| Routing surfaces checked | {reachability['routing_surfaces_checked']} |",
        f"| Runtime rules checked | {reachability['runtime_rules_checked']} |",
        f"| Browsing tested | {browsing['tested']} |",
        f"| Browsing qualified | {browsing['qualified']} |",
        f"| Browsing stable / reserve | {browsing['stable']} / {browsing['reserve']} |",
        f"| Browsing automatic | {browsing['automatic']} |",
        f"| Browsing rejected | {browsing['rejected']} |",
        f"| AI tested | {ai['tested']} |",
        f"| AI selector failures | {ai['selector_failures']} |",
    ]
    for name, count in service_counts.items():
        lines.append(f"| AI {name} qualified | {count} |")
    fail_closed = ai.get("service_fail_closed", [])
    lines.extend(
        [
            f"| AI service fail-closed | {', '.join(fail_closed) if fail_closed else 'none'} |",
            "",
            "This proof contains aggregate metadata only; node names, servers, credentials, and subscription URLs are intentionally excluded.",
            "",
        ]
    )
    return "\n".join(lines)
