"""Reuse one successful full main validation for the exact production SHA."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

TRUSTED_WORKFLOW_PATH = ".github/workflows/publish.yml"
TRUSTED_BRANCH = "main"


@dataclass(frozen=True, slots=True)
class TrustedValidation:
    run_id: int
    head_sha: str


def select_trusted_validation(
    runs: Iterable[Mapping[str, Any] | object],
    *,
    sha: str,
) -> TrustedValidation | None:
    """Return a completed successful main push run for exactly sha."""

    for raw in runs:
        if not isinstance(raw, Mapping):
            continue
        run_id = raw.get("id")
        if (
            raw.get("event") != "push"
            or raw.get("status") != "completed"
            or raw.get("conclusion") != "success"
            or raw.get("head_branch") != TRUSTED_BRANCH
            or raw.get("head_sha") != sha
            or raw.get("path") != TRUSTED_WORKFLOW_PATH
            or not isinstance(run_id, int)
            or isinstance(run_id, bool)
        ):
            continue
        return TrustedValidation(run_id=run_id, head_sha=sha)
    return None
