"""Explicit production release progress without duplicating publication mechanics."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .errors import ValidationError


class ReleasePhase(StrEnum):
    PREPARED = "prepared"
    QUALIFIED = "qualified"
    PROMOTED = "promoted"
    PUBLISHED = "published"
    VERIFIED = "verified"


@dataclass(slots=True)
class ReleaseProgress:
    """Track the application-layer release state around the proven KV transaction."""

    publish: bool
    _history: list[ReleasePhase] = field(default_factory=list)

    @property
    def phase(self) -> str:
        if not self._history:
            return "not_started"
        return self._history[-1].value

    @property
    def history(self) -> tuple[str, ...]:
        return tuple(item.value for item in self._history)

    def advance(self, phase: ReleasePhase) -> None:
        if phase in self._history:
            raise ValidationError(f"release phase {phase.value!r} cannot be entered twice")
        expected = self._expected_next()
        if phase is not expected:
            raise ValidationError(
                f"release phase transition is invalid: expected {expected.value!r}, got {phase.value!r}"
            )
        self._history.append(phase)

    def _expected_next(self) -> ReleasePhase:
        if not self._history:
            return ReleasePhase.PREPARED
        current = self._history[-1]
        if current is ReleasePhase.PREPARED:
            return ReleasePhase.QUALIFIED
        if current is ReleasePhase.QUALIFIED:
            return ReleasePhase.PROMOTED
        if current is ReleasePhase.PROMOTED:
            return ReleasePhase.PUBLISHED if self.publish else ReleasePhase.VERIFIED
        if current is ReleasePhase.PUBLISHED:
            return ReleasePhase.VERIFIED
        raise ValidationError("verified release has no further phase transition")

    def safe_summary(self) -> dict[str, object]:
        return {
            "phase": self.phase,
            "history": list(self.history),
            "publication_requested": self.publish,
        }
