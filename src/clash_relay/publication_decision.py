"""Single fail-closed authority for production publication trigger decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .errors import ValidationError


class PublicationMode(StrEnum):
    DRY_RUN = "dry-run"
    PUBLISH = "publish"


@dataclass(frozen=True, slots=True)
class PublicationDecision:
    mode: PublicationMode
    reason: str
    event_name: str | None

    @property
    def should_publish(self) -> bool:
        return self.mode is PublicationMode.PUBLISH


def _decision(*, publish: bool, reason: str, event_name: str | None) -> PublicationDecision:
    return PublicationDecision(
        mode=PublicationMode.PUBLISH if publish else PublicationMode.DRY_RUN,
        reason=reason,
        event_name=event_name,
    )


def resolve_publication_decision(
    *,
    explicit_publish: bool | None = None,
    event_name: str | None = None,
    manual_publish: str | bool | None = None,
) -> PublicationDecision:
    """Resolve the canonical publication decision without workflow shell logic.

    Automatic GitHub events are always dry-run. Explicit local publication and
    manual workflow dispatch remain available. Unsupported events fail closed
    unless an explicit local publish/dry-run flag already selected the mode.
    """

    if event_name in {"push", "schedule"}:
        return _decision(publish=False, reason="automatic_event", event_name=event_name)

    if explicit_publish is not None:
        return _decision(
            publish=explicit_publish,
            reason="explicit_flag",
            event_name=event_name,
        )

    if not event_name:
        return _decision(publish=False, reason="no_event", event_name=None)

    if event_name == "workflow_dispatch":
        if isinstance(manual_publish, bool):
            publish = manual_publish
        else:
            publish = str(manual_publish or "").strip().lower() == "true"
        return _decision(
            publish=publish,
            reason="manual_dispatch",
            event_name=event_name,
        )

    raise ValidationError(f"unsupported production publication event {event_name!r}")
