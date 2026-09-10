"""Typed boundary for the canonical production lifecycle result.

The production lifecycle still owns orchestration and may build its aggregate
result from lower-level mapping reports. This module validates the stable
CLI-facing contract before that result leaves the process. Unknown internal
fields are preserved so observability can evolve without weakening the typed
core state contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .errors import ValidationError
from .release_reliability import ReleasePhase


class ProductionLifecycleStatus(StrEnum):
    PASSED = "passed"
    SKIPPED = "skipped"


class ProductionPublicationStatus(StrEnum):
    PUBLISHED = "published"
    DRY_RUN = "dry-run"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class ProductionLifecycleResult:
    status: ProductionLifecycleStatus
    publication_status: ProductionPublicationStatus
    release_phase: ReleasePhase | None
    reason: str | None
    warnings: tuple[str, ...]
    _document: dict[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ProductionLifecycleResult:
        """Validate the stable top-level lifecycle result contract fail closed."""

        try:
            status = ProductionLifecycleStatus(value.get("status"))
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "production lifecycle result has an invalid status"
            ) from exc

        try:
            publication_status = ProductionPublicationStatus(
                value.get("publication_status")
            )
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "production lifecycle result has an invalid publication status"
            ) from exc

        phase_value = value.get("release_phase")
        release_phase: ReleasePhase | None = None
        if phase_value is not None:
            try:
                release_phase = ReleasePhase(phase_value)
            except (TypeError, ValueError) as exc:
                raise ValidationError(
                    "production lifecycle result has an invalid release phase"
                ) from exc

        reason_value = value.get("reason")
        if reason_value is not None and not isinstance(reason_value, str):
            raise ValidationError("production lifecycle result reason must be text")
        reason = reason_value

        warnings_value = value.get("warnings", [])
        if not isinstance(warnings_value, list) or any(
            not isinstance(item, str) for item in warnings_value
        ):
            raise ValidationError(
                "production lifecycle result warnings must be a string list"
            )
        warnings = tuple(warnings_value)

        if status is ProductionLifecycleStatus.PASSED:
            if publication_status is ProductionPublicationStatus.NOT_APPLICABLE:
                raise ValidationError(
                    "passed production lifecycle result cannot be not_applicable"
                )
            if release_phase is None:
                raise ValidationError(
                    "passed production lifecycle result requires a release phase"
                )
        else:
            if publication_status is not ProductionPublicationStatus.NOT_APPLICABLE:
                raise ValidationError(
                    "skipped production lifecycle result must be not_applicable"
                )
            if not reason:
                raise ValidationError(
                    "skipped production lifecycle result requires a reason"
                )

        return cls(
            status=status,
            publication_status=publication_status,
            release_phase=release_phase,
            reason=reason,
            warnings=warnings,
            _document=dict(value),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the original aggregate result without dropping future safe fields."""

        return dict(self._document)
