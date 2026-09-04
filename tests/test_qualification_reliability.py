from __future__ import annotations

from clash_relay.qualification_reliability import (
    QualificationFailureCategory,
    classify_browsing_stage_failure,
)


def test_whole_probe_infrastructure_failure_is_retryable_transient() -> None:
    failure = classify_browsing_stage_failure(
        stage="browsing",
        message="no nodes passed browsing qualification",
        diagnostics={
            "tested_nodes": 6,
            "successful_samples": 0,
            "failed_samples": 18,
            "outcomes": {"probe_error": 12, "controller_http_503": 6},
        },
    )

    assert failure.category is QualificationFailureCategory.TRANSIENT
    assert failure.retryable is True


def test_partial_live_success_is_not_retryable() -> None:
    failure = classify_browsing_stage_failure(
        stage="browsing_rewrite",
        message="browsing qualification left provider empty",
        diagnostics={
            "tested_nodes": 6,
            "successful_samples": 4,
            "failed_samples": 14,
            "outcomes": {"success": 4, "probe_error": 14},
        },
    )

    assert failure.category is not QualificationFailureCategory.TRANSIENT
    assert failure.retryable is False


def test_core_rejection_is_never_retryable() -> None:
    failure = classify_browsing_stage_failure(
        stage="browsing",
        message="Mihomo rejected the browsing qualification configuration",
        diagnostics={},
    )

    assert failure.category is QualificationFailureCategory.CORE_REJECTION
    assert failure.retryable is False


def test_transport_admission_failure_is_policy_rejection() -> None:
    failure = classify_browsing_stage_failure(
        stage="transport",
        message="no general nodes passed UDP transport qualification",
        diagnostics={"tested_nodes": 8, "successful_samples": 0, "failed_samples": 8},
    )

    assert failure.category is QualificationFailureCategory.POLICY_REJECTION
    assert failure.retryable is False
