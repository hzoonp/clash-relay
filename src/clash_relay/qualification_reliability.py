"""Typed reliability contract for live production qualification stages.

Retry decisions must be based on structured, privacy-safe stage evidence rather
than exception-message matching. Only a narrow class of whole-probe transient
failures may be retried; policy, configuration, transport-admission, core, and
protocol failures remain fail closed on the first occurrence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .errors import ValidationError


class QualificationFailureCategory(StrEnum):
    TRANSIENT = "transient"
    POLICY_REJECTION = "policy_rejection"
    CORE_REJECTION = "core_rejection"
    CONFIGURATION = "configuration"
    PROCESS_ERROR = "process_error"
    PROTOCOL_ERROR = "protocol_error"


@dataclass(frozen=True, slots=True)
class QualificationFailure:
    stage: str
    category: QualificationFailureCategory
    retryable: bool


class QualificationStageRejected(ValidationError):
    """Structured in-process rejection for qualification orchestration."""

    def __init__(
        self,
        *,
        stage: str,
        category: QualificationFailureCategory,
        retryable: bool,
        diagnostics: dict[str, Any] | None = None,
        transport_diagnostics: dict[str, Any] | None = None,
    ) -> None:
        self.stage = stage
        self.category = category
        self.retryable = retryable and category is QualificationFailureCategory.TRANSIENT
        self.diagnostics = dict(diagnostics or {})
        self.transport_diagnostics = dict(transport_diagnostics or {})
        super().__init__(f"qualification stage {stage!r} rejected candidate [{category.value}]")

    def as_result(self) -> dict[str, Any]:
        """Return the stable CLI-facing rejection contract without stderr parsing."""

        result: dict[str, Any] = {
            "status": "rejected",
            "stage": self.stage,
            "failure_category": self.category.value,
            "retryable": self.retryable,
        }
        if self.diagnostics:
            result["diagnostics"] = self.diagnostics
        if self.transport_diagnostics:
            result["transport_diagnostics"] = self.transport_diagnostics
        return result


_CORE_MARKERS = (
    "mihomo rejected",
    "mihomo exited",
    "mihomo controller did not become ready",
    "providers did not populate",
    "failed to execute mihomo",
    "failed to start mihomo",
)
_CONFIGURATION_MARKERS = (
    " must ",
    " requires ",
    " missing",
    " invalid",
    " cannot ",
    "contains no ",
    "found no ",
    "disappeared",
)


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _is_transient_outcome(name: str) -> bool:
    if name in {"probe_error", "missing_delay"}:
        return True
    if not name.startswith("controller_http_"):
        return False
    try:
        status = int(name.removeprefix("controller_http_"))
    except ValueError:
        return False
    return status == 429 or 500 <= status <= 599


def _whole_browsing_probe_transient(diagnostics: dict[str, Any]) -> bool:
    tested = _int(diagnostics.get("tested_nodes"))
    successful = _int(diagnostics.get("successful_samples"))
    failed = _int(diagnostics.get("failed_samples"))
    outcomes = diagnostics.get("outcomes")
    if tested <= 0 or successful != 0 or failed <= 0 or not isinstance(outcomes, dict):
        return False
    nonzero = {
        str(name) for name, count in outcomes.items() if _int(count) > 0 and str(name) != "success"
    }
    return bool(nonzero) and all(_is_transient_outcome(name) for name in nonzero)


def classify_browsing_stage_failure(
    *,
    stage: str,
    message: str,
    diagnostics: dict[str, Any] | None = None,
) -> QualificationFailure:
    """Classify one browsing qualification failure without runtime identities."""

    normalized = f" {message.strip().lower()} "
    if any(marker in normalized for marker in _CORE_MARKERS):
        return QualificationFailure(
            stage=stage,
            category=QualificationFailureCategory.CORE_REJECTION,
            retryable=False,
        )

    if stage == "browsing" and _whole_browsing_probe_transient(diagnostics or {}):
        return QualificationFailure(
            stage=stage,
            category=QualificationFailureCategory.TRANSIENT,
            retryable=True,
        )

    if any(marker in normalized for marker in _CONFIGURATION_MARKERS):
        category = QualificationFailureCategory.CONFIGURATION
    else:
        category = QualificationFailureCategory.POLICY_REJECTION
    return QualificationFailure(stage=stage, category=category, retryable=False)


def parse_failure_category(value: Any) -> QualificationFailureCategory | None:
    if not isinstance(value, str):
        return None
    try:
        return QualificationFailureCategory(value)
    except ValueError:
        return None
