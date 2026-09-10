from __future__ import annotations

import pytest

from clash_relay.errors import ValidationError
from clash_relay.production_lifecycle_result import (
    ProductionLifecycleResult,
    ProductionLifecycleStatus,
    ProductionPublicationStatus,
)
from clash_relay.release_reliability import ReleasePhase


def test_passed_lifecycle_result_round_trips_without_dropping_safe_fields() -> None:
    raw = {
        "status": "passed",
        "publication_status": "dry-run",
        "release_phase": "verified",
        "generation": "generated",
        "production_pipeline": "passed",
        "warnings": ["optional_stage"],
        "future_safe_field": {"status": "ok"},
    }

    result = ProductionLifecycleResult.from_mapping(raw)

    assert result.status is ProductionLifecycleStatus.PASSED
    assert result.publication_status is ProductionPublicationStatus.DRY_RUN
    assert result.release_phase is ReleasePhase.VERIFIED
    assert result.reason is None
    assert result.warnings == ("optional_stage",)
    assert result.as_dict() == raw


def test_skipped_lifecycle_result_requires_not_applicable_and_reason() -> None:
    raw = {
        "status": "skipped",
        "publication_status": "not_applicable",
        "reason": "canonical_declarations_missing",
    }

    result = ProductionLifecycleResult.from_mapping(raw)

    assert result.status is ProductionLifecycleStatus.SKIPPED
    assert result.publication_status is ProductionPublicationStatus.NOT_APPLICABLE
    assert result.release_phase is None
    assert result.reason == "canonical_declarations_missing"
    assert result.warnings == ()
    assert result.as_dict() == raw


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"status": "unknown", "publication_status": "dry-run"}, "invalid status"),
        ({"status": 1, "publication_status": "dry-run"}, "invalid status"),
        (
            {"status": "passed", "publication_status": "unknown"},
            "invalid publication status",
        ),
        (
            {
                "status": "passed",
                "publication_status": False,
                "release_phase": "verified",
            },
            "invalid publication status",
        ),
        (
            {
                "status": "passed",
                "publication_status": "dry-run",
                "release_phase": "unknown",
            },
            "invalid release phase",
        ),
        (
            {
                "status": "passed",
                "publication_status": "dry-run",
                "release_phase": [],
            },
            "invalid release phase",
        ),
        (
            {
                "status": "passed",
                "publication_status": "dry-run",
                "release_phase": "verified",
                "warnings": "not-a-list",
            },
            "warnings must be a string list",
        ),
        (
            {
                "status": "passed",
                "publication_status": "not_applicable",
                "release_phase": "verified",
            },
            "cannot be not_applicable",
        ),
        (
            {"status": "passed", "publication_status": "dry-run"},
            "requires a release phase",
        ),
        (
            {
                "status": "skipped",
                "publication_status": "dry-run",
                "reason": "missing",
            },
            "must be not_applicable",
        ),
        (
            {"status": "skipped", "publication_status": "not_applicable"},
            "requires a reason",
        ),
    ],
)
def test_lifecycle_result_rejects_invalid_top_level_contract(
    raw: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        ProductionLifecycleResult.from_mapping(raw)
