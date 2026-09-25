"""Unified in-process qualification orchestration for private production candidates."""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ai_application import run_ai_qualification
from .browsing_application import run_browsing_qualification
from .carrier_qualification import run_carrier_qualification
from .errors import ValidationError
from .policy_document import load_policy_document
from .proxy_endpoint_qualification import (
    _source,
    accelerate_client_health_checks,
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

    ``unique`` counts distinct physical endpoints — the same node replicated
    into several runtime providers is one node; the default counts runtime
    entries.
    """

    providers = document.get("proxy-providers")
    counts: dict[str, int] = {}
    seen: set[tuple[str, str, str, str]] = set()
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
            if source == "other":
                continue
            if unique:
                key = (
                    source,
                    str(proxy.get("server")),
                    str(proxy.get("port")),
                    str(proxy.get("type")),
                )
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


def _removed_nodes_summary(
    *,
    host_report: dict[str, Any],
    endpoint_report: dict[str, Any],
    generated_source_counts: dict[str, int],
    generated_unique_source_counts: dict[str, int],
    final_document: dict[str, Any],
) -> dict[str, Any]:
    """Aggregate-only accounting of nodes removed by qualification.

    Surfaces the removal dimensions (source/region/protocol/failure category)
    and flags every subscription source whose entire runtime inventory was
    removed, so a silently emptied subscription always has an explainable,
    privacy-safe reason attached.
    """

    generated_entries = generated_source_counts
    generated_unique = generated_unique_source_counts
    final_entries = _source_node_counts(final_document)
    final_unique = _source_node_counts(final_document, unique=True)
    by_source_failure_category: dict[str, dict[str, int]] = {}
    for report in (host_report, endpoint_report):
        for source, categories in (report.get("by_source_failure_category") or {}).items():
            bucket = by_source_failure_category.setdefault(str(source), {})
            for category, count in (categories or {}).items():
                bucket[str(category)] = bucket.get(str(category), 0) + int(count or 0)
    sources_fully_removed = []
    for source in sorted(set(generated_unique) | set(final_unique)):
        generated_unique_nodes = generated_unique.get(source, 0)
        final_unique_nodes = final_unique.get(source, 0)
        if generated_unique_nodes == 0 or final_unique_nodes > 0:
            continue
        sources_fully_removed.append(
            {
                "source": source,
                "unique_nodes": generated_unique_nodes,
                "final_unique_nodes": final_unique_nodes,
                "runtime_entries": generated_entries.get(source, 0),
                "final_runtime_entries": final_entries.get(source, 0),
                "by_failure_category": dict(
                    sorted(by_source_failure_category.get(source, {}).items())
                ),
            }
        )
    return {
        # Quarantine accounting is deduplicated to unique physical endpoints;
        # runtime_entries keeps the raw entry count for the same removals.
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

    The browsing tiers map onto scheduling directly (Stable group first, its
    Reserve group as fallback); runner endpoint/hostname evidence is admission
    evidence only. Client runtime URLTest remains the scheduling authority for
    the general inventory.
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
    # Snapshot per-source counts before preflight stages prune the payloads.
    generated_source_counts = _source_node_counts(generated_document)
    generated_unique_source_counts = _source_node_counts(generated_document, unique=True)
    proxy_host_resolution = quarantine_unresolvable_proxy_hosts(generated_document)
    endpoint_qualification = quarantine_unreachable_tcp_endpoints(
        generated_document, workers=workers
    )
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
        "removed_nodes": _removed_nodes_summary(
            host_report=proxy_host_resolution,
            endpoint_report=endpoint_qualification,
            generated_source_counts=generated_source_counts,
            generated_unique_source_counts=generated_unique_source_counts,
            final_document=final_document,
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
