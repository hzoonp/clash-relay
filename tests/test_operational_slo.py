from __future__ import annotations

import json

from clash_relay.operational_slo import (
    ProductionOutcome,
    append_slo_attempt,
    build_slo_attempt,
    empty_slo_state,
    parse_slo_bytes,
    qualification_failure_category,
    qualification_retry_attempted,
    scheduler_tuning_evidence,
    slo_summary,
)
from clash_relay.qualification_reliability import (
    QualificationFailureCategory,
    QualificationStageRejected,
)


def _sha(character: str) -> str:
    return character * 64


def test_slo_summary_measures_rejections_retry_guard_duration_and_churn() -> None:
    state = empty_slo_state()
    attempts = [
        build_slo_attempt(
            outcome=ProductionOutcome.PASSED,
            duration_ms=100.0,
            candidate_sha256=_sha("a"),
            candidate_bytes=1000,
            promotion_guard_checked=True,
            epoch=1,
        ),
        build_slo_attempt(
            outcome=ProductionOutcome.QUALIFICATION_REJECTED,
            duration_ms=200.0,
            candidate_sha256=_sha("b"),
            candidate_bytes=1100,
            qualification_failure_category="transient",
            retry_attempted=True,
            epoch=2,
        ),
        build_slo_attempt(
            outcome=ProductionOutcome.PASSED,
            duration_ms=300.0,
            candidate_sha256=_sha("b"),
            candidate_bytes=1100,
            retry_attempted=True,
            retry_recovered=True,
            promotion_guard_checked=True,
            epoch=3,
        ),
        build_slo_attempt(
            outcome=ProductionOutcome.PROMOTION_BLOCKED,
            duration_ms=400.0,
            candidate_sha256=_sha("c"),
            candidate_bytes=900,
            promotion_guard_checked=True,
            promotion_guard_blocked=True,
            epoch=4,
        ),
    ]
    for attempt in attempts:
        state = append_slo_attempt(state, attempt)

    summary = slo_summary(state)
    assert summary["attempts"] == 4
    assert summary["qualification_rejections"] == 1
    assert summary["qualification_rejection_rate"] == 0.25
    assert summary["qualification_rejections_by_category"] == {"transient": 1}
    assert summary["retry_attempts"] == 2
    assert summary["retry_recoveries"] == 1
    assert summary["retry_recovery_rate"] == 0.5
    assert summary["promotion_guard_checks"] == 3
    assert summary["promotion_guard_blocks"] == 1
    assert summary["promotion_guard_block_rate"] == 0.3333
    assert summary["lifecycle_duration_ms"] == {"p50": 200.0, "p95": 400.0, "max": 400.0}
    assert summary["candidate_transitions"] == 3
    assert summary["candidate_changes"] == 2
    assert summary["candidate_churn_rate"] == 0.6667
    assert summary["latest_candidate_bytes_delta"] == -200
    assert summary["scheduler_tuning_evidence"]["status"] == "insufficient_evidence"
    assert summary["scheduler_tuning_evidence"]["automatic_tuning_allowed"] is False


def test_scheduler_tuning_review_requires_longitudinal_attempts_and_transitions() -> None:
    insufficient = scheduler_tuning_evidence(
        {
            "attempts": 11,
            "candidate_transitions": 10,
            "lifecycle_duration_ms": {"p50": 10.0, "p95": 20.0, "max": 30.0},
        }
    )
    assert insufficient["status"] == "insufficient_evidence"
    assert insufficient["review_allowed"] is False
    assert insufficient["missing_evidence"] == ["longitudinal_attempts"]

    ready = scheduler_tuning_evidence(
        {
            "attempts": 12,
            "candidate_transitions": 4,
            "lifecycle_duration_ms": {"p50": 10.0, "p95": 20.0, "max": 30.0},
        }
    )
    assert ready["status"] == "eligible_for_review"
    assert ready["review_allowed"] is True
    assert ready["automatic_tuning_allowed"] is False
    assert ready["missing_evidence"] == []


def test_slo_state_is_bounded_and_rejects_unstructured_payloads() -> None:
    state = empty_slo_state()
    for index in range(65):
        state = append_slo_attempt(
            state,
            build_slo_attempt(
                outcome=ProductionOutcome.PASSED,
                duration_ms=10.0,
                candidate_sha256=f"{index:064x}",
                candidate_bytes=index,
                epoch=index,
            ),
        )
    assert len(state["attempts"]) == 60
    assert state["attempts"][0]["epoch"] == 5

    parsed, status = parse_slo_bytes(b'{"version":1,"attempts":[{"secret":"TOKEN"}]}')
    assert status == "invalid"
    assert parsed == empty_slo_state()
    assert "TOKEN" not in json.dumps(parsed)


def test_typed_qualification_failure_is_recovered_without_message_parsing() -> None:
    rejected = QualificationStageRejected(
        stage="browsing",
        category=QualificationFailureCategory.TRANSIENT,
        retryable=True,
    )
    wrapped = RuntimeError("opaque outer failure")
    wrapped.__cause__ = rejected

    assert qualification_failure_category(wrapped) == "transient"
    assert qualification_retry_attempted(wrapped) is True


def test_slo_attempt_contains_only_aggregate_allowlisted_fields() -> None:
    attempt = build_slo_attempt(
        outcome=ProductionOutcome.QUALIFICATION_REJECTED,
        duration_ms=12.5,
        candidate_sha256=_sha("d"),
        candidate_bytes=2048,
        qualification_failure_category="policy_rejection",
        epoch=100,
    )
    assert set(attempt) == {
        "epoch",
        "outcome",
        "duration_ms",
        "retry_attempted",
        "retry_recovered",
        "promotion_guard_checked",
        "promotion_guard_blocked",
        "candidate_sha256",
        "candidate_bytes",
        "qualification_failure_category",
    }
