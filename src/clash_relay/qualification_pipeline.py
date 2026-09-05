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
from .errors import ValidationError
from .policy_document import load_policy_document
from .qualification_reliability import QualificationStageRejected
from .runtime_graph import CandidateArtifact
from .service_qualification import harden_declared_service_client_paths
from .util import atomic_write, load_yaml_file

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
    return {
        "status": "qualified",
        "policy_model_version": policy_model_version,
        "stages": [{"name": row.name, "fingerprint": row.fingerprint} for row in stages],
        "timings_ms": {
            "browsing_transport": browsing_elapsed_ms,
            "ai": ai_elapsed_ms,
            "service_client_path": runtime_elapsed_ms,
            "total": _elapsed_ms(pipeline_started),
        },
        "browsing": {
            "status": browsing_summary.get("status"),
            "automatic_nodes": browsing_summary.get("automatic_nodes", 0),
            "stage_attempts": browsing_attempts_used,
            "recovered_by_retry": recovered_failure_category is not None,
            "recovered_failure_category": recovered_failure_category,
        },
        "ai": {
            "status": ai_summary.get("status"),
            "qualification_mode": ai_summary.get("diagnostics", {}).get(
                "qualification_mode", "unknown"
            )
            if isinstance(ai_summary.get("diagnostics"), dict)
            else "unknown",
            "client_path_status": runtime_summary.get("status"),
            "client_path_hardened_services": runtime_summary.get("hardened_services", 0),
            "client_path_services": hardened_service_names,
        },
    }
