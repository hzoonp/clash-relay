"""Read-only reconciliation for ambiguous versioned release commits.

A reconciliation pass never publishes, retries, or compensates. It only compares
observable KV state with the candidate and the known pre-attempt production
bytes so an operator can distinguish a proven commit, a proven non-commit, and
state that still requires manual investigation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .errors import PublicationError
from .release_bundle import PublisherFactory, parse_release_pointer, release_id_for, release_keys


class ReconciliationStatus(StrEnum):
    COMMITTED = "committed"
    NOT_COMMITTED = "not_committed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ReleaseReconciliation:
    status: ReconciliationStatus
    production_changed: bool | str
    release_id: str
    reason: str
    production_matches_candidate: bool | None
    current_release_id: str | None
    previous_release_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "production_changed": self.production_changed,
            "release_id": self.release_id,
            "reason": self.reason,
            "production_matches_candidate": self.production_matches_candidate,
            "current_release_id": self.current_release_id,
            "previous_release_id": self.previous_release_id,
            "requires_manual_action": self.status is ReconciliationStatus.UNKNOWN,
        }


def _unknown(
    *,
    release_id: str,
    reason: str,
    production_changed: bool | str = "unknown",
    production_matches_candidate: bool | None = None,
    current_release_id: str | None = None,
    previous_release_id: str | None = None,
) -> ReleaseReconciliation:
    return ReleaseReconciliation(
        status=ReconciliationStatus.UNKNOWN,
        production_changed=production_changed,
        release_id=release_id,
        reason=reason,
        production_matches_candidate=production_matches_candidate,
        current_release_id=current_release_id,
        previous_release_id=previous_release_id,
    )


def reconcile_release_bundle(
    *,
    factory: PublisherFactory,
    production_key: str,
    candidate_content: bytes,
    previous_content: bytes | None,
) -> ReleaseReconciliation:
    """Classify the final observable state after a commit-unknown publication.

    ``previous_content`` is the exact production value observed before the
    ambiguous attempt. ``None`` represents a first release. The function is
    deliberately read-only: unknown or contradictory evidence is returned as
    ``unknown`` instead of attempting another write.
    """

    candidate_id = release_id_for(candidate_content)
    previous_id = release_id_for(previous_content) if previous_content is not None else None
    keys = release_keys(production_key)

    try:
        production = factory(keys.production).read()
        current_raw = factory(keys.current_pointer).read()
        previous_raw = factory(keys.previous_pointer).read()
    except PublicationError:
        return _unknown(release_id=candidate_id, reason="read_failed")

    try:
        current_id = parse_release_pointer(current_raw)
        observed_previous_id = parse_release_pointer(previous_raw)
    except PublicationError:
        return _unknown(
            release_id=candidate_id,
            reason="invalid_pointer",
            production_matches_candidate=production == candidate_content,
        )

    production_matches_candidate = production == candidate_content
    current_matches_candidate = current_id == candidate_id

    if production_matches_candidate and current_matches_candidate:
        if previous_content == candidate_content:
            return ReleaseReconciliation(
                status=ReconciliationStatus.COMMITTED,
                production_changed=False,
                release_id=candidate_id,
                reason="candidate_already_live",
                production_matches_candidate=True,
                current_release_id=current_id,
                previous_release_id=observed_previous_id,
            )
        if previous_content is None:
            if observed_previous_id is None:
                return ReleaseReconciliation(
                    status=ReconciliationStatus.COMMITTED,
                    production_changed=True,
                    release_id=candidate_id,
                    reason="first_release_fully_visible",
                    production_matches_candidate=True,
                    current_release_id=current_id,
                    previous_release_id=None,
                )
            return _unknown(
                release_id=candidate_id,
                reason="candidate_live_with_unexpected_previous_pointer",
                production_changed=True,
                production_matches_candidate=True,
                current_release_id=current_id,
                previous_release_id=observed_previous_id,
            )
        if observed_previous_id == previous_id:
            return ReleaseReconciliation(
                status=ReconciliationStatus.COMMITTED,
                production_changed=True,
                release_id=candidate_id,
                reason="release_transaction_fully_visible",
                production_matches_candidate=True,
                current_release_id=current_id,
                previous_release_id=observed_previous_id,
            )
        return _unknown(
            release_id=candidate_id,
            reason="candidate_live_with_incomplete_pointer_transaction",
            production_changed=True,
            production_matches_candidate=True,
            current_release_id=current_id,
            previous_release_id=observed_previous_id,
        )

    if previous_content is None:
        definitely_not_committed = production is None and not current_matches_candidate
    else:
        definitely_not_committed = production == previous_content and not current_matches_candidate

    if definitely_not_committed:
        return ReleaseReconciliation(
            status=ReconciliationStatus.NOT_COMMITTED,
            production_changed=False,
            release_id=candidate_id,
            reason="pre_attempt_production_still_live",
            production_matches_candidate=False,
            current_release_id=current_id,
            previous_release_id=observed_previous_id,
        )

    return _unknown(
        release_id=candidate_id,
        reason="observable_state_is_contradictory_or_unrelated",
        production_matches_candidate=production_matches_candidate,
        current_release_id=current_id,
        previous_release_id=observed_previous_id,
    )
