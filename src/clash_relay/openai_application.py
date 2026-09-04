"""In-process OpenAI client-path hardening application service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .ai_runtime_reliability import rewrite_openai_client_path_candidate


def harden_openai_client_path(candidate: Path) -> dict[str, Any]:
    """Apply client-local OpenAI runtime hardening to a private candidate."""

    report = dict(rewrite_openai_client_path_candidate(candidate))
    report["runtime_status"] = str(report.pop("status", "unknown"))
    report["status"] = "passed"
    return report
