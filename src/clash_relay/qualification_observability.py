"""Explicit aggregate-only projection of private qualification evidence."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .errors import ValidationError
from .runtime_names import valid_source_id, validate_runtime_source_labels

_STAGES = ("hostname", "endpoint", "browsing", "transport", "ai", "service_hardening", "final")
_CATEGORY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_EVIDENCE_STATUS = {"passed", "failed", "inconclusive"}
_EVIDENCE_SOURCE = {"live", "cache", "mixed", "none"}
_REGION = re.compile(r"^(?:hk|tw|sg|jp|us|kr|other)$")
_PROTOCOL = re.compile(
    r"^(?:ss|ssr|vmess|vless|trojan|http|socks5|snell|hysteria|hysteria2|tuic|anytls|wireguard|ssh|mieru|masque|unknown)$"
)


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError("qualification aggregate count is invalid")
    return value


def _labels(value: Any, pattern: re.Pattern[str] | None = None) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValidationError("qualification aggregate dimension is invalid")
    result: dict[str, int] = {}
    for label, count in sorted(value.items()):
        safe_label = (
            valid_source_id(label)
            if pattern is None
            else isinstance(label, str) and pattern.fullmatch(label) is not None
        )
        if not safe_label:
            raise ValidationError("qualification aggregate label is invalid")
        result[label] = _count(count)
    return result


def safe_qualification_observability(
    value: dict[str, Any], *, known_source_ids: Iterable[str] | None = None
) -> dict[str, Any]:
    """Project accounting and service evidence without copying private strings."""
    try:
        source_map = (
            validate_runtime_source_labels(known_source_ids)
            if known_source_ids is not None
            else None
        )
    except ValueError as exc:
        raise ValidationError("qualification source aliases are ambiguous") from exc

    def source_counts(value: Any) -> dict[str, int]:
        labels = _labels(value)
        if source_map is None:
            return labels
        result: dict[str, int] = {}
        for label, count in labels.items():
            if label not in source_map:
                raise ValidationError("qualification provenance contains an unknown source")
            canonical = source_map[label]
            result[canonical] = result.get(canonical, 0) + count
        return dict(sorted(result.items()))

    stages = value.get("removed_by_stage", {})
    sources = value.get("sources_fully_removed", [])
    if not isinstance(stages, dict) or not isinstance(sources, list):
        raise ValidationError("qualification provenance is invalid")
    removed_by_stage: dict[str, Any] = {}
    for stage in _STAGES:
        row = stages.get(stage)
        if row is None:
            continue
        if not isinstance(row, dict):
            raise ValidationError("qualification stage provenance is invalid")
        counts: dict[str, dict[str, int]] = {}
        for kind in ("unique_nodes", "runtime_entries"):
            values = row.get(kind)
            if not isinstance(values, dict):
                raise ValidationError("qualification stage accounting is invalid")
            counts[kind] = {key: _count(values.get(key)) for key in ("before", "after", "removed")}
            if kind == "runtime_entries":
                counts[kind]["added"] = _count(values.get("added", 0))
        removed_by_stage[stage] = {
            **counts,
            "by_source": source_counts(row.get("by_source", {})),
            "unique_by_source": source_counts(row.get("unique_by_source", {})),
            "added_by_source": source_counts(row.get("added_by_source", {})),
            "by_region": _labels(row.get("by_region", {}), _REGION),
            "by_protocol": _labels(row.get("by_protocol", {}), _PROTOCOL),
            "failure_category": _labels(row.get("failure_category", {}), _CATEGORY),
        }
    fully_removed: list[dict[str, Any]] = []
    for row in sources:
        if not isinstance(row, dict):
            raise ValidationError("fully removed source provenance is invalid")
        source = row.get("source")
        removal_stage = row.get("removed_at_stage")
        if not valid_source_id(source):
            raise ValidationError("fully removed source label is invalid")
        if source_map is not None:
            if source not in source_map:
                raise ValidationError("fully removed source is unknown")
            source = source_map[source]
        if not isinstance(removal_stage, str) or removal_stage not in (*_STAGES, "unattributed"):
            raise ValidationError("fully removed source stage is invalid")
        fully_removed.append(
            {
                "source": source,
                "unique_nodes": _count(row.get("unique_nodes")),
                "final_unique_nodes": _count(row.get("final_unique_nodes")),
                "runtime_entries": _count(row.get("runtime_entries")),
                "final_runtime_entries": _count(row.get("final_runtime_entries")),
                "removed_at_stage": removal_stage,
                "by_stage": _labels(row.get("by_stage", {}), _CATEGORY),
                "by_failure_category": _labels(row.get("by_failure_category", {}), _CATEGORY),
            }
        )
    ai = value.get("ai", {})
    projected_evidence = "ai_service_evidence" in value
    evidence = (
        value["ai_service_evidence"]
        if projected_evidence
        else ai.get("service_evidence", {})
        if isinstance(ai, dict)
        else {}
    )
    if not isinstance(evidence, dict):
        raise ValidationError("AI service evidence is invalid")
    safe_evidence: dict[str, Any] = {}
    for service, row in sorted(evidence.items()):
        if (
            not isinstance(service, str)
            or _CATEGORY.fullmatch(service) is None
            or not isinstance(row, dict)
        ):
            raise ValidationError("AI service evidence label is invalid")
        status = row.get("evidence_status")
        source = row.get("evidence_source")
        if status not in _EVIDENCE_STATUS or source not in _EVIDENCE_SOURCE:
            raise ValidationError("AI service evidence state is invalid")
        if projected_evidence:
            blocked_count = _count(row.get("blocked_critical_endpoint_count"))
        else:
            blocked = row.get("blocked_critical_endpoints", [])
            if not isinstance(blocked, list):
                raise ValidationError("AI blocked endpoint aggregate is invalid")
            blocked_count = len(blocked)
        if not isinstance(row.get("systemic_failure_detected"), bool) or not isinstance(
            row.get("lkg_fresh"), bool
        ):
            raise ValidationError("AI service evidence flags are invalid")
        safe_evidence[service] = {
            "evidence_status": status,
            "evidence_source": source,
            "systemic_failure_detected": row["systemic_failure_detected"],
            "lkg_fresh": row["lkg_fresh"],
            **{
                key: _count(row.get(key))
                for key in ("live_tested", "live_passed", "live_failed", "inconclusive")
            },
            "blocked_critical_endpoint_count": blocked_count,
        }
    return {
        "qualification_removed_unique_nodes": _count(
            value.get("qualification_removed_unique_nodes", 0)
        ),
        "qualification_removed_runtime_entries": _count(
            value.get("qualification_removed_runtime_entries", 0)
        ),
        "removed_by_stage": removed_by_stage,
        "sources_fully_removed": sorted(fully_removed, key=lambda row: row["source"]),
        "ai_service_evidence": safe_evidence,
    }


def render_qualification_observability_markdown(
    value: dict[str, Any], *, known_source_ids: Iterable[str] | None = None
) -> str:
    """Render source removal attribution and AI evidence for Actions."""
    safe = safe_qualification_observability(value, known_source_ids=known_source_ids)
    lines = [
        "## Qualification provenance",
        "",
        f"Removed: **{safe['qualification_removed_unique_nodes']} unique nodes / {safe['qualification_removed_runtime_entries']} runtime entries**",
        "",
        "| Stage | Unique nodes before / after / removed | Runtime entries before / after / removed / added | By source | By region | By protocol | Aggregate failure reason |",
        "| --- | ---: | ---: | --- | --- | --- | --- |",
    ]
    for stage, row in safe["removed_by_stage"].items():
        unique = row["unique_nodes"]
        runtime = row["runtime_entries"]
        reason = (
            ", ".join(f"{name}: {count}" for name, count in row["failure_category"].items())
            or "none"
        )
        dimensions = [
            ", ".join(f"{name}: {count}" for name, count in row[key].items()) or "none"
            for key in ("by_source", "by_region", "by_protocol")
        ]
        lines.append(
            f"| {stage} | {unique['before']} / {unique['after']} / {unique['removed']} | {runtime['before']} / {runtime['after']} / {runtime['removed']} / {runtime['added']} | {' | '.join(dimensions)} | {reason} |"
        )
    lines.extend(
        [
            "",
            "| Fully removed source | removed_at_stage | unique_nodes | runtime_entries | Aggregate failure reason |",
            "| --- | --- | ---: | ---: | --- |",
        ]
    )
    for row in safe["sources_fully_removed"]:
        reason = ", ".join(f"{name}: {count}" for name, count in row["by_failure_category"].items())
        lines.append(
            f"| {row['source']} | {row['removed_at_stage']} | {row['unique_nodes']} | {row['runtime_entries']} | {reason} |"
        )
    lines.extend(
        [
            "",
            "| AI service | evidence_status | evidence_source | systemic_failure_detected | lkg_fresh | live tested / passed / failed / inconclusive | blocked_critical_endpoint_count |",
            "| --- | --- | --- | --- | --- | ---: | ---: |",
        ]
    )
    for service, row in safe["ai_service_evidence"].items():
        counts = " / ".join(
            str(row[key]) for key in ("live_tested", "live_passed", "live_failed", "inconclusive")
        )
        lines.append(
            f"| {service} | {row['evidence_status']} | {row['evidence_source']} | {str(row['systemic_failure_detected']).lower()} | {str(row['lkg_fresh']).lower()} | {counts} | {row['blocked_critical_endpoint_count']} |"
        )
    lines.append("")
    return "\n".join(lines)
