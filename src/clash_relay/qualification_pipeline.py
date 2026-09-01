"""Unified staged qualification orchestration for private production candidates.

The existing browsing/transport and AI qualification implementations remain the
compatibility executors. P16 gives them one owner and stops production from
mutating the generated candidate through unrelated workflow steps: each stage
receives a private copy and the final validated artifact is emitted explicitly.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .runtime_graph import CandidateArtifact
from .util import atomic_write, load_yaml_file


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
        # Child diagnostics can contain provider/runtime details. Keep the public
        # orchestration error deliberately aggregate-only.
        raise ValidationError(f"{name} qualification stage rejected the candidate")
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{name} qualification stage returned invalid JSON") from exc
    if not isinstance(document, dict) or document.get("status") not in {"qualified", "passed"}:
        raise ValidationError(f"{name} qualification stage did not report success")
    return document


def _json_text(document: dict[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


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
    """Run browsing+transport then AI qualification using immutable file stages."""

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

    generated = stage_dir / "01-generated.yaml"
    browsing = stage_dir / "02-browsing-transport.yaml"
    ai = stage_dir / "03-ai.yaml"
    try:
        shutil.copyfile(candidate, generated)
        shutil.copyfile(generated, browsing)
    except OSError as exc:
        raise ValidationError("failed to prepare private qualification stages") from exc

    generated_artifact = _artifact(generated, "generated")
    browsing_command = [
        python,
        str(scripts / "qualify_browsing.py"),
        "--candidate",
        str(browsing),
        "--policies",
        str(policies),
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
    browsing_summary = _run_json_stage("browsing/transport", browsing_command)
    atomic_write(browsing_report, _json_text(browsing_summary))
    browsing_artifact = _artifact(browsing, "browsing_transport_qualified")

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
        str(policies),
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
    ai_summary = _run_json_stage("AI", ai_command)
    atomic_write(ai_report, _json_text(ai_summary))
    ai_artifact = _artifact(ai, "ai_qualified")

    try:
        shutil.copyfile(ai, output)
    except OSError as exc:
        raise ValidationError("failed to emit final qualified candidate") from exc
    final_artifact = _artifact(output, "final_qualified")

    stages = (
        QualificationStage("generated", generated, generated_artifact.fingerprint),
        QualificationStage("browsing_transport_qualified", browsing, browsing_artifact.fingerprint),
        QualificationStage("ai_qualified", ai, ai_artifact.fingerprint),
        QualificationStage("final_qualified", output, final_artifact.fingerprint),
    )
    return {
        "status": "qualified",
        "stages": [{"name": row.name, "fingerprint": row.fingerprint} for row in stages],
        "browsing": {
            "status": browsing_summary.get("status"),
            "automatic_nodes": browsing_summary.get("automatic_nodes", 0),
        },
        "ai": {
            "status": ai_summary.get("status"),
            "qualification_mode": ai_summary.get("diagnostics", {}).get(
                "qualification_mode", "unknown"
            )
            if isinstance(ai_summary.get("diagnostics"), dict)
            else "unknown",
        },
    }
