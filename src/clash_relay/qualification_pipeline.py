"""Unified in-process qualification orchestration for private production candidates."""

from __future__ import annotations

import json
import shutil
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ai_application import run_ai_qualification
from .browsing_application import run_browsing_qualification
from .carrier_qualification import run_carrier_qualification
from .classify import proxy_fingerprint
from .errors import ValidationError
from .policy_document import load_policy_document
from .proxy_endpoint_qualification import (
    _region,
    _source,
    accelerate_client_health_checks,
    cap_endpoint_reserve_pools,
    quarantine_unreachable_tcp_endpoints,
)
from .proxy_host_qualification import quarantine_unresolvable_proxy_hosts
from .qualification_pipeline_result import QualificationPipelineResult
from .qualification_reliability import QualificationStageRejected
from .runtime_graph import CandidateArtifact
from .service_qualification import harden_declared_service_client_paths
from .service_qualification_result import service_qualification_results
from .util import atomic_write, dump_yaml, load_yaml_file

_BROWSING_STAGE_ATTEMPTS = 2
_BROWSING_RETRY_DELAY_SECONDS = 1.0
_STAGE_REASONS = {
    "browsing": "browsing_qualification_failed",
    "ai": "ai_qualification_failed",
    "service_hardening": "service_client_path_hardening",
}
_PROVENANCE_STAGE_ORDER = ("hostname", "endpoint", "browsing", "ai", "service_hardening")
_SAFE_DIAGNOSTIC_KEYS = frozenset(
    {
        "qualification_mode",
        "attempts_per_node",
        "required_successes",
        "tested_nodes",
        "qualified_nodes",
        "stable_nodes",
        "reserve_nodes",
        "failed_nodes",
        "successful_samples",
        "failed_samples",
        "tcp_qualified_nodes",
        "udp_qualified_nodes",
        "quic_path_nodes",
        "tcp_failed_nodes",
        "udp_failed_nodes",
        "static_udp_disabled_nodes",
        "selector_failures",
        "tcp_attempts",
        "tcp_required_successes",
        "udp_timeout_ms",
        "outcomes",
        "core_quarantine_status",
        "core_quarantined_nodes",
        "core_quarantined_runtime_entries",
        "core_quarantined_sources",
        "core_quarantined_proxy_types",
    }
)


@dataclass(frozen=True, slots=True)
class QualificationStage:
    name: str
    path: Path
    fingerprint: str


def _artifact(path: Path, stage: str) -> CandidateArtifact:
    document = load_yaml_file(path)
    if not isinstance(document, dict):
        raise ValidationError(f"qualification stage {stage!r} is not a YAML mapping")
    return CandidateArtifact.from_document(stage, document)


def _safe_rejection_context(error: QualificationStageRejected) -> str | None:
    """Render aggregate-only typed diagnostics without runtime identities."""

    safe: dict[str, Any] = {
        "stage": error.stage,
        "failure_category": error.category.value,
        "retryable": error.retryable,
    }
    for section_name, section in (
        ("diagnostics", error.diagnostics),
        ("transport_diagnostics", error.transport_diagnostics),
    ):
        filtered = {key: section[key] for key in sorted(section) if key in _SAFE_DIAGNOSTIC_KEYS}
        if filtered:
            safe[section_name] = filtered
    return json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stage_error(name: str, error: QualificationStageRejected) -> ValidationError:
    context = _safe_rejection_context(error)
    suffix = f"; aggregate diagnostics={context}" if context else ""
    return ValidationError(
        f"{name} qualification stage rejected the candidate [{error.category.value}]{suffix}"
    )


def _json_text(document: dict[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)


def _qualification_policy_input(policies: Path) -> tuple[Path, int]:
    """Validate and pass through the required Policy Model v2 manifest."""

    policy_document = load_policy_document(policies)
    return policies, policy_document.model_version


def _carrier_report(carrier_input: Path | None) -> dict[str, Any]:
    """Aggregate-only carrier report; pluggable via a self-hosted payload file."""

    if carrier_input is None:
        return run_carrier_qualification()
    try:
        payload = json.loads(carrier_input.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(
            f"carrier qualification input {carrier_input.name!r} could not be read"
        ) from exc
    if not isinstance(payload, dict):
        raise ValidationError("carrier qualification input must be an object")
    return run_carrier_qualification(payload)


def _source_node_counts(document: dict[str, Any], *, unique: bool = False) -> dict[str, int]:
    """Count nodes per subscription source (aggregate labels only).

    ``unique`` counts distinct full proxy fingerprints — the same node replicated
    into several runtime providers is one node; the default counts runtime
    entries.
    """

    providers = document.get("proxy-providers")
    counts: dict[str, int] = {}
    seen: set[tuple[str, str]] = set()
    if not isinstance(providers, dict):
        return counts
    for provider in providers.values():
        payload = provider.get("payload") if isinstance(provider, dict) else None
        if not isinstance(payload, list):
            continue
        for proxy in payload:
            if not isinstance(proxy, dict):
                continue
            source = _source(proxy.get("name"))
            if unique:
                key = (source, proxy_fingerprint(proxy))
                if key in seen:
                    continue
                seen.add(key)
            counts[source] = counts.get(source, 0) + 1
    return counts


def _merged_category(
    host_report: dict[str, Any], endpoint_report: dict[str, Any], key: str
) -> dict[str, int]:
    merged: dict[str, int] = {}
    for report in (host_report, endpoint_report):
        for name, count in (report.get(key) or {}).items():
            merged[str(name)] = merged.get(str(name), 0) + int(count or 0)
    return dict(sorted(merged.items()))


def _entry_inventory(document: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Map runtime entry name -> aggregate attributes (privacy-safe labels)."""

    providers = document.get("proxy-providers")
    inventory: dict[str, dict[str, str]] = {}
    if not isinstance(providers, dict):
        return inventory
    for provider_name, provider in providers.items():
        payload = provider.get("payload") if isinstance(provider, dict) else None
        if not isinstance(payload, list):
            continue
        region = _region(str(provider_name))
        for proxy in payload:
            if not isinstance(proxy, dict):
                continue
            name = proxy.get("name")
            if not isinstance(name, str):
                continue
            source = _source(name)
            protocol = str(proxy.get("type", "unknown"))
            entry_key = f"{provider_name}\0{name}"
            if entry_key in inventory:
                raise ValidationError("qualification inventory has duplicate provider entries")
            inventory[entry_key] = {
                "source": source,
                "region": region,
                "protocol": protocol,
                "unique": f"{source}|{proxy_fingerprint(proxy)}",
            }
    return inventory


def _stage_delta(
    before: dict[str, dict[str, str]],
    after: dict[str, dict[str, str]],
    *,
    stage: str,
    reason: str,
    failure_category: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate the removal delta between two stage inventories."""

    removed_names = set(before) - set(after)
    added_names = set(after) - set(before)
    removed_rows = [before[name] for name in sorted(removed_names)]
    before_unique = {row["unique"] for row in before.values()}
    after_unique = {row["unique"] for row in after.values()}
    if after_unique - before_unique or (added_names and stage != "service_hardening"):
        raise ValidationError(f"{stage} qualification stage added runtime inventory")
    removed_unique_set = before_unique - after_unique
    removed_unique = len(removed_unique_set)
    unique_sources = {
        row["unique"]: row["source"]
        for row in before.values()
        if row["unique"] in removed_unique_set
    }
    unique_by_source: Counter[str] = Counter(unique_sources.values())
    by_source: Counter[str] = Counter(row["source"] for row in removed_rows)
    by_region: Counter[str] = Counter(row["region"] for row in removed_rows)
    by_protocol: Counter[str] = Counter(row["protocol"] for row in removed_rows)
    categories = (
        {str(name): int(count or 0) for name, count in (failure_category or {}).items()}
        if failure_category
        else ({reason: len(removed_rows)} if removed_rows else {})
    )
    runtime_entries = {
        "before": len(before),
        "after": len(after),
        "removed": len(removed_rows),
    }
    if added_names:
        runtime_entries["added"] = len(added_names)
    return {
        "stage": stage,
        "reason": reason,
        "unique_nodes": {
            "before": len(before_unique),
            "after": len(after_unique),
            "removed": removed_unique,
        },
        "runtime_entries": runtime_entries,
        "by_source": dict(sorted(by_source.items())),
        "unique_by_source": dict(sorted(unique_by_source.items())),
        "added_by_source": dict(
            sorted(Counter(after[name]["source"] for name in added_names).items())
        ),
        "by_region": dict(sorted(by_region.items())),
        "by_protocol": dict(sorted(by_protocol.items())),
        "failure_category": categories,
    }


def _transport_stage_entry(
    browsing_summary: dict[str, Any], browsing_delta: dict[str, Any]
) -> dict[str, Any]:
    """Transport stage accounting: filters narrow, payload entries remain."""

    after_entries = browsing_delta["runtime_entries"]["after"]
    after_unique = browsing_delta["unique_nodes"]["after"]
    transport_report = browsing_summary.get("transport_qualification")
    transport_report = transport_report if isinstance(transport_report, dict) else {}
    diagnostics = browsing_summary.get("diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    core_entries = int(diagnostics.get("core_quarantined_runtime_entries", 0) or 0)
    return {
        "stage": "transport",
        "reason": "automatic_groups_narrowed_to_qualified_transports",
        "unique_nodes": {"before": after_unique, "after": after_unique, "removed": 0},
        "runtime_entries": {"before": after_entries, "after": after_entries, "removed": 0},
        "by_source": {},
        "unique_by_source": {},
        "added_by_source": {},
        "by_region": {},
        "by_protocol": {},
        "failure_category": {},
        "core_quarantine": {
            "runtime_entries": core_entries,
            "sources": diagnostics.get("core_quarantined_sources", []),
            "proxy_types": diagnostics.get("core_quarantined_proxy_types", []),
        },
        "automatic_groups": {
            "general_automatic_nodes": transport_report.get("general_automatic_nodes"),
            "udp_automatic_nodes": transport_report.get("udp_automatic_nodes"),
        },
    }


def _build_stage_accounting(
    *,
    preflight_inventory: dict[str, dict[str, str]],
    post_host_inventory: dict[str, dict[str, str]],
    post_endpoint_inventory: dict[str, dict[str, str]],
    browsing_document: dict[str, Any],
    browsing_summary: dict[str, Any],
    ai_document: dict[str, Any],
    service_document: dict[str, Any],
    final_document: dict[str, Any],
    host_report: dict[str, Any],
    endpoint_report: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Per-stage removal accounting across the whole qualification chain."""

    deltas: dict[str, dict[str, Any]] = {}
    deltas["hostname"] = _stage_delta(
        preflight_inventory,
        post_host_inventory,
        stage="hostname",
        reason="dns_unresolvable",
        failure_category=host_report.get("by_failure_category") or {},
    )
    deltas["endpoint"] = _stage_delta(
        post_host_inventory,
        post_endpoint_inventory,
        stage="endpoint",
        reason="tcp_endpoint_unreachable",
        failure_category=endpoint_report.get("by_failure_category") or {},
    )
    deltas["browsing"] = _stage_delta(
        post_endpoint_inventory,
        _entry_inventory(browsing_document),
        stage="browsing",
        reason=_STAGE_REASONS["browsing"],
    )
    deltas["transport"] = _transport_stage_entry(browsing_summary, deltas["browsing"])
    deltas["ai"] = _stage_delta(
        _entry_inventory(browsing_document),
        _entry_inventory(ai_document),
        stage="ai",
        reason=_STAGE_REASONS["ai"],
    )
    deltas["service_hardening"] = _stage_delta(
        _entry_inventory(ai_document),
        _entry_inventory(service_document),
        stage="service_hardening",
        reason=_STAGE_REASONS["service_hardening"],
    )
    deltas["final"] = _stage_delta(
        _entry_inventory(service_document),
        _entry_inventory(final_document),
        stage="final",
        reason="none",
    )
    if (
        deltas["final"]["unique_nodes"]["removed"] > 0
        or deltas["final"]["runtime_entries"]["removed"] > 0
    ):
        raise ValidationError("final qualification stage removed runtime inventory")
    return deltas


def _sources_fully_removed(
    *,
    stage_deltas: dict[str, dict[str, Any]],
    generated_entries: dict[str, int],
    generated_unique: dict[str, int],
    final_document: dict[str, Any],
    host_report: dict[str, Any],
    endpoint_report: dict[str, Any],
) -> list[dict[str, Any]]:
    """Flag sources whose entire runtime inventory was removed, with provenance.

    ``removed_at_stage`` names the stage that removed the last surviving entry;
    every flagged source carries non-empty aggregate failure reasons — an
    unattributable removal is explicitly marked ``unattributed`` instead of
    silently passing without explanation.
    """

    final_entries = _source_node_counts(final_document)
    final_unique = _source_node_counts(final_document, unique=True)
    per_stage_source = {
        stage: stage_deltas[stage]["by_source"] for stage in _PROVENANCE_STAGE_ORDER
    }
    results: list[dict[str, Any]] = []
    for source in sorted(set(generated_unique) | set(generated_entries)):
        generated_unique_nodes = generated_unique.get(source, 0)
        if generated_unique_nodes == 0 or final_unique.get(source, 0) > 0:
            continue
        remaining = generated_entries.get(source, 0)
        removed_at_stage: str | None = None
        by_stage: dict[str, int] = {}
        categories: dict[str, int] = {}
        for stage in _PROVENANCE_STAGE_ORDER:
            removed = int(per_stage_source[stage].get(source, 0) or 0)
            if not removed:
                continue
            by_stage[stage] = removed
            remaining = max(remaining - removed, 0)
            if stage == "hostname":
                stage_categories = (host_report.get("by_source_failure_category") or {}).get(
                    source, {}
                )
            elif stage == "endpoint":
                stage_categories = (endpoint_report.get("by_source_failure_category") or {}).get(
                    source, {}
                )
            else:
                stage_categories = {stage_deltas[stage]["reason"]: removed}
            for category, count in stage_categories.items():
                categories[str(category)] = categories.get(str(category), 0) + int(count or 0)
            if remaining == 0 and removed_at_stage is None:
                removed_at_stage = stage
        if removed_at_stage is None:
            removed_at_stage = "unattributed"
        if not categories:
            categories = {"unattributed": generated_entries.get(source, 0)}
        results.append(
            {
                "source": source,
                "unique_nodes": generated_unique_nodes,
                "final_unique_nodes": final_unique.get(source, 0),
                "runtime_entries": generated_entries.get(source, 0),
                "final_runtime_entries": final_entries.get(source, 0),
                "removed_at_stage": removed_at_stage,
                "by_stage": dict(sorted(by_stage.items())),
                "by_failure_category": dict(sorted(categories.items())),
            }
        )
    return results


def _removed_nodes_summary(
    *,
    host_report: dict[str, Any],
    endpoint_report: dict[str, Any],
    generated_source_counts: dict[str, int],
    generated_unique_source_counts: dict[str, int],
    final_document: dict[str, Any],
    stage_deltas: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Legacy aggregate removal block (runtime-entry semantics).

    ``by_source``/``by_region``/``by_protocol``/``by_failure_category`` count
    runtime entries, not logical nodes; the full-fingerprint logical-node
    view lives in ``unique_nodes`` and in the top-level ``removed_by_stage``
    and ``qualification_removed_unique_nodes`` fields.
    """

    sources_fully_removed = _sources_fully_removed(
        stage_deltas=stage_deltas,
        generated_entries=generated_source_counts,
        generated_unique=generated_unique_source_counts,
        final_document=final_document,
        host_report=host_report,
        endpoint_report=endpoint_report,
    )
    return {
        "unique_nodes": int(host_report.get("unique_quarantined_nodes", 0) or 0)
        + int(endpoint_report.get("unique_quarantined_nodes", 0) or 0),
        "runtime_entries": int(host_report.get("quarantined", 0) or 0)
        + int(endpoint_report.get("quarantined", 0) or 0),
        "by_source": _merged_category(host_report, endpoint_report, "by_source"),
        "by_region": _merged_category(host_report, endpoint_report, "by_region"),
        "by_protocol": _merged_category(host_report, endpoint_report, "by_protocol"),
        "by_failure_category": _merged_category(
            host_report, endpoint_report, "by_failure_category"
        ),
        "sources_fully_removed": sources_fully_removed,
    }


def _quality_tier_summary(
    *,
    host_report: dict[str, Any],
    endpoint_report: dict[str, Any],
    browsing_summary: dict[str, Any],
) -> dict[str, Any]:
    """Aggregate robust/reserve/quarantined evidence per qualification unit.

    Browsing Stable pools and general preferred pools exclude endpoint reserve
    evidence; Reserve pools remain eligible for failover. Client URLTest still
    chooses among each pool's qualified nodes on the user's actual network.
    """

    browsing_diagnostics = browsing_summary.get("diagnostics")
    if not isinstance(browsing_diagnostics, dict):
        browsing_diagnostics = {}
    return {
        "authority": "runner_preflight_evidence_plus_client_urltest",
        "runner_endpoint_evidence": {
            "robust": int(endpoint_report.get("robust_endpoints", 0) or 0),
            "reserve": int(endpoint_report.get("reserve_endpoints", 0) or 0),
            "quarantined": int(endpoint_report.get("quarantined", 0) or 0),
        },
        "runner_hostname_evidence": {
            "robust": int(host_report.get("resolved", 0) or 0),
            "reserve": int(host_report.get("dns_inconclusive", 0) or 0),
            "quarantined": int(host_report.get("quarantined", 0) or 0),
        },
        "browsing_node_evidence": {
            "robust": int(browsing_summary.get("stable_nodes", 0) or 0),
            "reserve": int(browsing_summary.get("reserve_nodes", 0) or 0),
            "quarantined": int(browsing_diagnostics.get("failed_nodes", 0) or 0),
        },
    }


def run_qualification_pipeline(
    *,
    candidate: Path,
    output: Path,
    policies: Path,
    mihomo_bin: Path,
    stage_dir: Path,
    browsing_report: Path,
    ai_report: Path,
    workers: int = 12,
    history: Path | None = None,
    history_key: Path | None = None,
    next_history: Path | None = None,
    cache: Path | None = None,
    cache_key: Path | None = None,
    next_cache: Path | None = None,
    carrier_input: Path | None = None,
) -> dict[str, Any]:
    """Run immutable browsing, AI admission, and declared service hardening stages."""

    pipeline_started = time.perf_counter()
    if workers < 1:
        raise ValidationError("qualification workers must be at least 1")
    for label, paths in {
        "scheduler history": (history, history_key, next_history),
        "AI cache": (cache, cache_key, next_cache),
    }.items():
        provided = tuple(path is not None for path in paths)
        if any(provided) and not all(provided):
            raise ValidationError(f"{label} inputs must be supplied together")

    stage_dir.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    browsing_report.parent.mkdir(parents=True, exist_ok=True)
    ai_report.parent.mkdir(parents=True, exist_ok=True)

    qualification_policies, policy_model_version = _qualification_policy_input(policies)

    generated = stage_dir / "01-generated.yaml"
    browsing = stage_dir / "02-browsing-transport.yaml"
    ai = stage_dir / "03-ai.yaml"
    service_runtime = stage_dir / "04-service-client-path.yaml"
    try:
        shutil.copyfile(candidate, generated)
    except OSError as exc:
        raise ValidationError("failed to prepare private qualification stages") from exc

    generated_artifact = _artifact(generated, "generated")
    generated_document = load_yaml_file(generated)
    if not isinstance(generated_document, dict):
        raise ValidationError("proxy hostname qualification candidate is not a YAML mapping")
    # Snapshot per-source counts and the full entry inventory before preflight
    # stages prune the payloads; the inventories feed per-stage attribution.
    generated_source_counts = _source_node_counts(generated_document)
    generated_unique_source_counts = _source_node_counts(generated_document, unique=True)
    preflight_inventory = _entry_inventory(generated_document)
    proxy_host_resolution = quarantine_unresolvable_proxy_hosts(generated_document)
    post_host_inventory = _entry_inventory(generated_document)
    endpoint_reserve_names: set[str] = set()
    endpoint_qualification = quarantine_unreachable_tcp_endpoints(
        generated_document, workers=workers, reserve_names=endpoint_reserve_names
    )
    post_endpoint_inventory = _entry_inventory(generated_document)
    atomic_write(generated, dump_yaml(generated_document, header=True))
    generated_artifact = _artifact(generated, "proxy_host_qualified")
    browsing_started = time.perf_counter()
    browsing_summary: dict[str, Any] | None = None
    browsing_attempts_used = 0
    recovered_failure_category: str | None = None
    for attempt in range(_BROWSING_STAGE_ATTEMPTS):
        browsing_attempts_used = attempt + 1
        if attempt:
            time.sleep(_BROWSING_RETRY_DELAY_SECONDS)
        try:
            shutil.copyfile(generated, browsing)
        except OSError as exc:
            raise ValidationError("failed to prepare browsing qualification stage") from exc
        try:
            browsing_summary = run_browsing_qualification(
                candidate=browsing,
                policies=qualification_policies,
                mihomo_bin=mihomo_bin,
                workers=workers,
                history=history,
                history_key=history_key,
                next_history=next_history,
            )
            break
        except QualificationStageRejected as exc:
            if not exc.retryable or browsing_attempts_used >= _BROWSING_STAGE_ATTEMPTS:
                raise _stage_error("browsing/transport", exc) from exc
            recovered_failure_category = exc.category.value
    if browsing_summary is None:  # pragma: no cover - defensive invariant
        raise ValidationError("browsing/transport qualification produced no result")
    atomic_write(browsing_report, _json_text(browsing_summary))
    browsing_artifact = _artifact(browsing, "browsing_transport_qualified")
    browsing_elapsed_ms = _elapsed_ms(browsing_started)

    try:
        shutil.copyfile(browsing, ai)
    except OSError as exc:
        raise ValidationError("failed to prepare AI qualification stage") from exc
    ai_started = time.perf_counter()
    ai_summary = run_ai_qualification(
        candidate=ai,
        policies=qualification_policies,
        mihomo_bin=mihomo_bin,
        workers=workers,
        cache=cache,
        cache_key=cache_key,
        next_cache=next_cache,
    )
    aggregate_service_results = service_qualification_results(ai_summary)
    atomic_write(ai_report, _json_text(ai_summary))
    ai_artifact = _artifact(ai, "ai_qualified")
    ai_elapsed_ms = _elapsed_ms(ai_started)

    try:
        shutil.copyfile(ai, service_runtime)
    except OSError as exc:
        raise ValidationError("failed to prepare service client-path hardening stage") from exc
    runtime_started = time.perf_counter()
    runtime_summary = harden_declared_service_client_paths(
        candidate=service_runtime,
        policies=qualification_policies,
    )
    service_document = load_yaml_file(service_runtime)
    if not isinstance(service_document, dict):
        raise ValidationError("qualified runtime candidate is not a YAML mapping")
    cap_endpoint_reserve_pools(service_document, endpoint_reserve_names)
    accelerated_groups = accelerate_client_health_checks(service_document)
    atomic_write(service_runtime, dump_yaml(service_document, header=True))
    runtime_artifact = _artifact(service_runtime, "service_client_path_hardened")
    runtime_elapsed_ms = _elapsed_ms(runtime_started)

    try:
        shutil.copyfile(service_runtime, output)
    except OSError as exc:
        raise ValidationError("failed to emit final qualified candidate") from exc
    final_artifact = _artifact(output, "final_qualified")

    stages = (
        QualificationStage("generated", generated, generated_artifact.fingerprint),
        QualificationStage("browsing_transport_qualified", browsing, browsing_artifact.fingerprint),
        QualificationStage("ai_qualified", ai, ai_artifact.fingerprint),
        QualificationStage(
            "service_client_path_hardened",
            service_runtime,
            runtime_artifact.fingerprint,
        ),
        QualificationStage("final_qualified", output, final_artifact.fingerprint),
    )
    services = runtime_summary.get("services")
    hardened_service_names = sorted(services) if isinstance(services, dict) else []
    browsing_block = {
        "status": browsing_summary.get("status"),
        "automatic_nodes": browsing_summary.get("automatic_nodes", 0),
        "stage_attempts": browsing_attempts_used,
        "recovered_by_retry": recovered_failure_category is not None,
        "recovered_failure_category": recovered_failure_category,
        "core_quarantine_status": browsing_summary.get("diagnostics", {}).get(
            "core_quarantine_status", "not_needed"
        )
        if isinstance(browsing_summary.get("diagnostics"), dict)
        else "not_needed",
        "core_quarantined_nodes": int(
            browsing_summary.get("diagnostics", {}).get("core_quarantined_nodes", 0) or 0
        )
        if isinstance(browsing_summary.get("diagnostics"), dict)
        else 0,
        "core_quarantined_runtime_entries": int(
            browsing_summary.get("diagnostics", {}).get("core_quarantined_runtime_entries", 0) or 0
        )
        if isinstance(browsing_summary.get("diagnostics"), dict)
        else 0,
    }
    final_document = load_yaml_file(output)
    if not isinstance(final_document, dict):
        raise ValidationError("final qualified candidate is not a YAML mapping")
    browsing_document = load_yaml_file(browsing)
    if not isinstance(browsing_document, dict):
        raise ValidationError("browsing qualified stage is not a YAML mapping")
    ai_document = load_yaml_file(ai)
    if not isinstance(ai_document, dict):
        raise ValidationError("ai qualified stage is not a YAML mapping")
    stage_deltas = _build_stage_accounting(
        preflight_inventory=preflight_inventory,
        post_host_inventory=post_host_inventory,
        post_endpoint_inventory=post_endpoint_inventory,
        browsing_document=browsing_document,
        browsing_summary=browsing_summary,
        ai_document=ai_document,
        service_document=service_document,
        final_document=final_document,
        host_report=proxy_host_resolution,
        endpoint_report=endpoint_qualification,
    )
    sources_fully_removed = _sources_fully_removed(
        stage_deltas=stage_deltas,
        generated_entries=generated_source_counts,
        generated_unique=generated_unique_source_counts,
        final_document=final_document,
        host_report=proxy_host_resolution,
        endpoint_report=endpoint_qualification,
    )
    if any(
        row["removed_at_stage"] == "unattributed" or "unattributed" in row["by_failure_category"]
        for row in sources_fully_removed
    ):
        raise ValidationError("qualification removal lacks stage provenance")
    final_inventory = _entry_inventory(final_document)
    removed_unique_total = len(
        {row["unique"] for row in preflight_inventory.values()}
        - {row["unique"] for row in final_inventory.values()}
    )
    removed_entries_total = sum(
        stage_deltas[stage]["runtime_entries"]["removed"] for stage in _PROVENANCE_STAGE_ORDER
    )
    result = {
        "status": "qualified",
        "policy_model_version": policy_model_version,
        "stages": [{"name": row.name, "fingerprint": row.fingerprint} for row in stages],
        "timings_ms": {
            "browsing_transport": browsing_elapsed_ms,
            "ai": ai_elapsed_ms,
            "service_client_path": runtime_elapsed_ms,
            "total": _elapsed_ms(pipeline_started),
        },
        "browsing": browsing_block,
        "proxy_host_resolution": proxy_host_resolution,
        "endpoint_qualification": endpoint_qualification,
        "qualification_removed_unique_nodes": removed_unique_total,
        "qualification_removed_runtime_entries": removed_entries_total,
        "removed_by_stage": stage_deltas,
        "sources_fully_removed": sources_fully_removed,
        "removed_nodes": _removed_nodes_summary(
            host_report=proxy_host_resolution,
            endpoint_report=endpoint_qualification,
            generated_source_counts=generated_source_counts,
            generated_unique_source_counts=generated_unique_source_counts,
            final_document=final_document,
            stage_deltas=stage_deltas,
        ),
        "node_quality_tiers": _quality_tier_summary(
            host_report=proxy_host_resolution,
            endpoint_report=endpoint_qualification,
            browsing_summary=browsing_summary,
        ),
        "accelerated_health_check_groups": accelerated_groups,
        "ai": {
            "status": ai_summary.get("status"),
            "qualification_mode": ai_summary.get("diagnostics", {}).get(
                "qualification_mode", "unknown"
            )
            if isinstance(ai_summary.get("diagnostics"), dict)
            else "unknown",
            "service_evidence": ai_summary.get("service_evidence", {}),
            "services": aggregate_service_results,
            "client_path_status": runtime_summary.get("status"),
            "client_path_hardened_services": runtime_summary.get("hardened_services", 0),
            "client_path_services": hardened_service_names,
        },
        # Reachability evidence is reported in three separate authorities.
        # `global_preflight_reachable` is GitHub-Runner-side TCP endpoint
        # admission that only filters obviously dead endpoints; it must never
        # be read as China Telecom / Unicom / Mobile quality. Real carrier
        # quality is reserved for self-hosted carrier probes.
        "reachability": {
            "global_preflight_reachable": endpoint_qualification,
            "client_runtime_health": {
                "authority": "client_local_urltest",
                "probe_url": "https://cp.cloudflare.com/generate_204",
                "max_failed_times": {"browsing": 1, "regional_and_other": 2},
                "browsing": browsing_block,
                "ai_status": ai_summary.get("status"),
                "accelerated_health_check_groups": accelerated_groups,
            },
            "carrier_qualification": _carrier_report(carrier_input),
        },
    }
    return QualificationPipelineResult.from_mapping(result).as_dict()
