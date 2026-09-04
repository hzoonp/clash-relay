"""Unified staged qualification orchestration for private production candidates."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .policy_document import load_policy_document
from .runtime_graph import CandidateArtifact
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


def _safe_rejection_context(stdout: str) -> str | None:
    """Return child-provided aggregate diagnostics without runtime identities."""
    try:
        document = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(document, dict) or document.get("status") != "rejected":
        return None

    safe: dict[str, Any] = {}
    stage = document.get("stage")
    if isinstance(stage, str) and stage in {"browsing", "transport"}:
        safe["stage"] = stage
    for section_name in ("diagnostics", "transport_diagnostics"):
        section = document.get(section_name)
        if not isinstance(section, dict):
            continue
        filtered = {key: section[key] for key in sorted(section) if key in _SAFE_DIAGNOSTIC_KEYS}
        if filtered:
            safe[section_name] = filtered
    if not safe:
        return None
    return json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _run_json_stage(name: str, command: Sequence[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError as exc:
        raise ValidationError(f"failed to start {name} qualification stage") from exc
    if result.returncode != 0:
        # Child stderr may contain provider/runtime identities. Only surface the
        # explicitly whitelisted aggregate JSON contract emitted on stdout.
        context = _safe_rejection_context(result.stdout)
        suffix = f"; aggregate diagnostics={context}" if context else ""
        raise ValidationError(f"{name} qualification stage rejected the candidate{suffix}")
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{name} qualification stage returned invalid JSON") from exc
    if not isinstance(document, dict) or document.get("status") not in {"qualified", "passed"}:
        raise ValidationError(f"{name} qualification stage did not report success")
    return document


def _json_text(document: dict[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)


def _qualification_policy_input(policies: Path, stage_dir: Path) -> tuple[Path, int]:
    """Normalize only Policy Model v2 while preserving the historical v1 contract.

    The standalone qualification runner has always allowed tests and advanced
    callers to hand child executors a lightweight or even deferred v1 policy
    path. Production validates the project before entering this function, so
    eagerly schema-validating every v1 path here would be an unnecessary
    compatibility break. V2 manifests, however, must be composed before the
    legacy child executors can consume them.
    """

    if not policies.is_file():
        return policies, 1
    raw = load_yaml_file(policies)
    if not isinstance(raw, dict) or raw.get("version") != 2 or "fragments" not in raw:
        return policies, 1

    policy_document = load_policy_document(policies)
    normalized_policies = stage_dir / "00-policies.normalized.yaml"
    atomic_write(normalized_policies, dump_yaml(policy_document.document, header=False))
    return normalized_policies, policy_document.model_version


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
    script_dir: Path | None = None,
    python_executable: str | None = None,
) -> dict[str, Any]:
    """Run immutable browsing, AI admission, and client-path hardening stages."""
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

    scripts = Path("scripts") if script_dir is None else script_dir
    python = python_executable or sys.executable
    stage_dir.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    browsing_report.parent.mkdir(parents=True, exist_ok=True)
    ai_report.parent.mkdir(parents=True, exist_ok=True)

    qualification_policies, policy_model_version = _qualification_policy_input(policies, stage_dir)

    generated = stage_dir / "01-generated.yaml"
    browsing = stage_dir / "02-browsing-transport.yaml"
    ai = stage_dir / "03-ai.yaml"
    openai_runtime = stage_dir / "04-openai-client-path.yaml"
    try:
        shutil.copyfile(candidate, generated)
    except OSError as exc:
        raise ValidationError("failed to prepare private qualification stages") from exc

    generated_artifact = _artifact(generated, "generated")
    browsing_command = [
        python,
        str(scripts / "qualify_browsing.py"),
        "--candidate",
        str(browsing),
        "--policies",
        str(qualification_policies),
        "--mihomo-bin",
        str(mihomo_bin),
        "--workers",
        str(workers),
    ]
    if history is not None and history_key is not None and next_history is not None:
        browsing_command.extend(
            [
                "--history",
                str(history),
                "--history-key",
                str(history_key),
                "--next-history",
                str(next_history),
            ]
        )
    browsing_started = time.perf_counter()
    browsing_summary: dict[str, Any] | None = None
    browsing_attempts_used = 0
    for attempt in range(_BROWSING_STAGE_ATTEMPTS):
        browsing_attempts_used = attempt + 1
        if attempt:
            time.sleep(_BROWSING_RETRY_DELAY_SECONDS)
        try:
            shutil.copyfile(generated, browsing)
        except OSError as exc:
            raise ValidationError("failed to prepare browsing qualification stage") from exc
        try:
            browsing_summary = _run_json_stage("browsing/transport", browsing_command)
            break
        except ValidationError as exc:
            retryable = "qualification stage rejected the candidate" in str(exc)
            if not retryable or browsing_attempts_used >= _BROWSING_STAGE_ATTEMPTS:
                raise
    if browsing_summary is None:  # pragma: no cover - defensive invariant
        raise ValidationError("browsing/transport qualification produced no result")
    atomic_write(browsing_report, _json_text(browsing_summary))
    browsing_artifact = _artifact(browsing, "browsing_transport_qualified")
    browsing_elapsed_ms = _elapsed_ms(browsing_started)

    try:
        shutil.copyfile(browsing, ai)
    except OSError as exc:
        raise ValidationError("failed to prepare AI qualification stage") from exc
    ai_command = [
        python,
        str(scripts / "qualify_ai.py"),
        "--candidate",
        str(ai),
        "--policies",
        str(qualification_policies),
        "--mihomo-bin",
        str(mihomo_bin),
        "--workers",
        str(workers),
    ]
    if cache is not None and cache_key is not None and next_cache is not None:
        ai_command.extend(
            [
                "--cache",
                str(cache),
                "--cache-key",
                str(cache_key),
                "--next-cache",
                str(next_cache),
            ]
        )
    ai_started = time.perf_counter()
    ai_summary = _run_json_stage("AI", ai_command)
    atomic_write(ai_report, _json_text(ai_summary))
    ai_artifact = _artifact(ai, "ai_qualified")
    ai_elapsed_ms = _elapsed_ms(ai_started)

    try:
        shutil.copyfile(ai, openai_runtime)
    except OSError as exc:
        raise ValidationError("failed to prepare OpenAI client-path hardening stage") from exc
    runtime_started = time.perf_counter()
    runtime_summary = _run_json_stage(
        "OpenAI client-path",
        [
            python,
            str(scripts / "harden_openai_runtime.py"),
            "--candidate",
            str(openai_runtime),
        ],
    )
    runtime_artifact = _artifact(openai_runtime, "openai_client_path_hardened")
    runtime_elapsed_ms = _elapsed_ms(runtime_started)

    try:
        shutil.copyfile(openai_runtime, output)
    except OSError as exc:
        raise ValidationError("failed to emit final qualified candidate") from exc
    final_artifact = _artifact(output, "final_qualified")

    stages = (
        QualificationStage("generated", generated, generated_artifact.fingerprint),
        QualificationStage("browsing_transport_qualified", browsing, browsing_artifact.fingerprint),
        QualificationStage("ai_qualified", ai, ai_artifact.fingerprint),
        QualificationStage(
            "openai_client_path_hardened",
            openai_runtime,
            runtime_artifact.fingerprint,
        ),
        QualificationStage("final_qualified", output, final_artifact.fingerprint),
    )
    return {
        "status": "qualified",
        "policy_model_version": policy_model_version,
        "stages": [{"name": row.name, "fingerprint": row.fingerprint} for row in stages],
        "timings_ms": {
            "browsing_transport": browsing_elapsed_ms,
            "ai": ai_elapsed_ms,
            "openai_client_path": runtime_elapsed_ms,
            "total": _elapsed_ms(pipeline_started),
        },
        "browsing": {
            "status": browsing_summary.get("status"),
            "automatic_nodes": browsing_summary.get("automatic_nodes", 0),
            "stage_attempts": browsing_attempts_used,
        },
        "ai": {
            "status": ai_summary.get("status"),
            "qualification_mode": ai_summary.get("diagnostics", {}).get(
                "qualification_mode", "unknown"
            )
            if isinstance(ai_summary.get("diagnostics"), dict)
            else "unknown",
            "client_path_status": runtime_summary.get("status"),
            "client_path_selection": runtime_summary.get("selection", "unknown"),
            "client_path_regions": runtime_summary.get("runtime_regions", 0),
        },
    }
