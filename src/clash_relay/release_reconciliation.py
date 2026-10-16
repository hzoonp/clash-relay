"""Read-only reconciliation for ambiguous versioned release commits.

A reconciliation pass never publishes, retries, or compensates. It only compares
observable KV state with the candidate and the known pre-attempt production
bytes so an operator can distinguish a proven commit, a proven non-commit, and
state that still requires manual investigation.

One observation cannot date itself. Seeing the pre-attempt production bytes with
no pending intent is consistent both with a write that never landed and with an
observation path that still lags a write the attempt already verified. Because a
KV read is not an atomic snapshot, that reading is only reported as
``not_committed`` when the caller supplies evidence that its observation is at
least as fresh as the attempt's own read path; otherwise it is reported as
``indeterminate`` and requires a repeated read after the propagation window.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .errors import PublicationError
from .release_bundle import PublisherFactory, parse_release_pointer, release_id_for, release_keys
from .release_transaction import parse_release_transaction


class ReconciliationStatus(StrEnum):
    COMMITTED = "committed"
    NOT_COMMITTED = "not_committed"
    INDETERMINATE = "indeterminate"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ReleaseReconciliation:
    """One read-only verdict.

    ``observation_freshness`` states why a stale-looking observation was accepted
    as ``not_committed``: ``"fresh"`` means the caller asserted dated read
    evidence, and ``"unsupported"`` means no freshness evidence exists so the
    observation is reported as ``indeterminate`` instead. ``None`` means the
    observation proves the verdict by itself.
    """

    status: ReconciliationStatus
    production_changed: bool | str
    release_id: str
    reason: str
    production_matches_candidate: bool | None
    current_release_id: str | None
    previous_release_id: str | None
    observation_freshness: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "production_changed": self.production_changed,
            "release_id": self.release_id,
            "reason": self.reason,
            "production_matches_candidate": self.production_matches_candidate,
            "current_release_id": self.current_release_id,
            "previous_release_id": self.previous_release_id,
            "observation_freshness": self.observation_freshness,
            "requires_manual_action": self.status
            in {ReconciliationStatus.INDETERMINATE, ReconciliationStatus.UNKNOWN},
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
    fresh_read_evidence: bool = False,
) -> ReleaseReconciliation:
    """Classify the final observable state after a commit-unknown publication.

    ``previous_content`` is the exact production value observed before the
    ambiguous attempt. ``None`` represents a first release. ``fresh_read_evidence``
    is the caller's assertion that this observation is at least as well dated as
    the attempt's own verified read path; without it, seeing the pre-attempt
    bytes is not sufficient to prove a non-commit. The function is deliberately
    read-only: unknown or contradictory evidence is returned as ``unknown``
    instead of attempting another write.
    """

    candidate_id = release_id_for(candidate_content)
    previous_id = release_id_for(previous_content) if previous_content is not None else None
    keys = release_keys(production_key)

    try:
        production = factory(keys.production).read()
        current_raw = factory(keys.current_pointer).read()
        previous_raw = factory(keys.previous_pointer).read()
        transaction_raw = factory(keys.transaction).read()
    except PublicationError:
        return _unknown(release_id=candidate_id, reason="read_failed")

    try:
        current_id = parse_release_pointer(current_raw)
        observed_previous_id = parse_release_pointer(previous_raw)
        transaction = parse_release_transaction(transaction_raw)
    except PublicationError:
        return _unknown(
            release_id=candidate_id,
            reason="invalid_pointer",
            production_matches_candidate=production == candidate_content,
        )

    production_matches_candidate = production == candidate_content
    current_matches_candidate = current_id == candidate_id

    if transaction is not None:
        if transaction.to_release_id != candidate_id or transaction.from_release_id != previous_id:
            return _unknown(
                release_id=candidate_id,
                reason="transaction_targets_another_release",
                production_matches_candidate=production_matches_candidate,
                current_release_id=current_id,
                previous_release_id=observed_previous_id,
            )
        if (
            not production_matches_candidate
            or not current_matches_candidate
            or observed_previous_id != previous_id
        ):
            return _unknown(
                release_id=candidate_id,
                reason="transaction_incomplete_or_not_yet_visible",
                production_matches_candidate=production_matches_candidate,
                current_release_id=current_id,
                previous_release_id=observed_previous_id,
            )

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
        pre_attempt_production_still_live = production is None and not current_matches_candidate
    else:
        pre_attempt_production_still_live = (
            production == previous_content and not current_matches_candidate
        )

    if pre_attempt_production_still_live:
        if not fresh_read_evidence:
            return ReleaseReconciliation(
                status=ReconciliationStatus.INDETERMINATE,
                production_changed="unknown",
                release_id=candidate_id,
                reason="pre_attempt_production_still_live",
                production_matches_candidate=False,
                current_release_id=current_id,
                previous_release_id=observed_previous_id,
                observation_freshness="unsupported",
            )
        return ReleaseReconciliation(
            status=ReconciliationStatus.NOT_COMMITTED,
            production_changed=False,
            release_id=candidate_id,
            reason="pre_attempt_production_still_live",
            production_matches_candidate=False,
            current_release_id=current_id,
            previous_release_id=observed_previous_id,
            observation_freshness="fresh",
        )

    return _unknown(
        release_id=candidate_id,
        reason="observable_state_is_contradictory_or_unrelated",
        production_matches_candidate=production_matches_candidate,
        current_release_id=current_id,
        previous_release_id=observed_previous_id,
    )
