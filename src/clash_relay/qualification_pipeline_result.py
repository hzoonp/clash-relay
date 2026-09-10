"""Typed boundary for the canonical qualification pipeline result."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .errors import ValidationError

_EXPECTED_STAGE_NAMES = (
    "generated",
    "browsing_transport_qualified",
    "ai_qualified",
    "service_client_path_hardened",
    "final_qualified",
)
_REQUIRED_TIMING_NAMES = (
    "browsing_transport",
    "ai",
    "service_client_path",
    "total",
)


class QualificationPipelineStatus(StrEnum):
    QUALIFIED = "qualified"


@dataclass(frozen=True, slots=True)
class QualificationStageResult:
    name: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class QualificationPipelineResult:
    """Validated aggregate qualification state with no runtime identities."""

    status: QualificationPipelineStatus
    policy_model_version: int
    stages: tuple[QualificationStageResult, ...]
    timings_ms: tuple[tuple[str, float], ...]
    browsing_stage_attempts: int
    browsing_recovered_by_retry: bool
    browsing_recovered_failure_category: str | None
    ai_client_path_status: str
    _document: dict[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> QualificationPipelineResult:
        """Validate the stable successful qualification contract fail closed."""

        status_value = value.get("status")
        if not isinstance(status_value, str):
            raise ValidationError("qualification pipeline result has an invalid status")
        try:
            status = QualificationPipelineStatus(status_value)
        except ValueError as exc:
            raise ValidationError("qualification pipeline result has an invalid status") from exc

        policy_model_version = _non_negative_int(
            value.get("policy_model_version"),
            "policy_model_version",
        )
        if policy_model_version < 2:
            raise ValidationError("qualification pipeline result requires Policy Model v2 or newer")

        raw_stages = value.get("stages")
        if not isinstance(raw_stages, list):
            raise ValidationError("qualification pipeline result stages must be a list")
        stages: list[QualificationStageResult] = []
        for raw_stage in raw_stages:
            if not isinstance(raw_stage, Mapping):
                raise ValidationError("qualification pipeline stage must be a mapping")
            name = raw_stage.get("name")
            fingerprint = raw_stage.get("fingerprint")
            if not isinstance(name, str) or not name:
                raise ValidationError("qualification pipeline stage name must be text")
            if not isinstance(fingerprint, str) or not _is_sha256(fingerprint):
                raise ValidationError("qualification pipeline stage fingerprint must be sha256")
            stages.append(QualificationStageResult(name=name, fingerprint=fingerprint))
        if tuple(stage.name for stage in stages) != _EXPECTED_STAGE_NAMES:
            raise ValidationError("qualification pipeline stages do not match the canonical order")

        raw_timings = value.get("timings_ms")
        if not isinstance(raw_timings, Mapping):
            raise ValidationError("qualification pipeline timings must be a mapping")
        timings: list[tuple[str, float]] = []
        for name in _REQUIRED_TIMING_NAMES:
            timings.append(
                (name, _non_negative_number(raw_timings.get(name), f"timings_ms.{name}"))
            )

        browsing = value.get("browsing")
        if not isinstance(browsing, Mapping):
            raise ValidationError("qualification pipeline browsing summary must be a mapping")
        if browsing.get("status") != "qualified":
            raise ValidationError("qualification pipeline browsing stage must be qualified")
        _non_negative_int(browsing.get("automatic_nodes"), "browsing.automatic_nodes")
        browsing_stage_attempts = _non_negative_int(
            browsing.get("stage_attempts"),
            "browsing.stage_attempts",
        )
        if browsing_stage_attempts not in {1, 2}:
            raise ValidationError("qualification pipeline browsing attempts must be 1 or 2")
        recovered = browsing.get("recovered_by_retry")
        if not isinstance(recovered, bool):
            raise ValidationError("qualification pipeline retry recovery flag must be boolean")
        recovered_category = browsing.get("recovered_failure_category")
        if recovered:
            if recovered_category != "transient":
                raise ValidationError(
                    "qualification pipeline recovered retry must report transient failure"
                )
            if browsing_stage_attempts != 2:
                raise ValidationError(
                    "qualification pipeline recovered retry requires the second attempt"
                )
        elif recovered_category is not None:
            raise ValidationError(
                "qualification pipeline non-retried result cannot report a recovered failure"
            )

        ai = value.get("ai")
        if not isinstance(ai, Mapping):
            raise ValidationError("qualification pipeline AI summary must be a mapping")
        if ai.get("status") != "qualified":
            raise ValidationError("qualification pipeline AI stage must be qualified")
        qualification_mode = ai.get("qualification_mode")
        if not isinstance(qualification_mode, str) or not qualification_mode:
            raise ValidationError("qualification pipeline AI mode must be text")
        services = ai.get("services")
        if not isinstance(services, Mapping):
            raise ValidationError("qualification pipeline AI services must be a mapping")
        client_path_status = ai.get("client_path_status")
        if client_path_status != "passed":
            raise ValidationError("qualification pipeline client path hardening must pass")
        _non_negative_int(
            ai.get("client_path_hardened_services"),
            "ai.client_path_hardened_services",
        )
        client_path_services = ai.get("client_path_services")
        if not isinstance(client_path_services, list) or any(
            not isinstance(item, str) or not item for item in client_path_services
        ):
            raise ValidationError(
                "qualification pipeline client path services must be a string list"
            )

        return cls(
            status=status,
            policy_model_version=policy_model_version,
            stages=tuple(stages),
            timings_ms=tuple(timings),
            browsing_stage_attempts=browsing_stage_attempts,
            browsing_recovered_by_retry=recovered,
            browsing_recovered_failure_category=recovered_category,
            ai_client_path_status=client_path_status,
            _document=dict(value),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the original aggregate result without dropping future safe fields."""

        return dict(self._document)


def _non_negative_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValidationError(
            f"qualification pipeline result {field} must be a non-negative integer"
        )
    return value


def _non_negative_number(value: Any, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise ValidationError(f"qualification pipeline result {field} must be non-negative")
    return float(value)


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
