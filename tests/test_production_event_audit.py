from __future__ import annotations

import pytest

from clash_relay.errors import ValidationError
from clash_relay.production_event_audit import audit_production_event_result
from clash_relay.production_lifecycle_result import ProductionLifecycleResult
from clash_relay.publication_decision import resolve_publication_decision


def _passed_result(
    *,
    publication_status: str = "dry-run",
    release_phase: str = "verified",
    release_status: str = "dry-run",
    promotion_guard: str = "skipped",
    **overrides: object,
) -> ProductionLifecycleResult:
    raw: dict[str, object] = {
        "status": "passed",
        "publication_status": publication_status,
        "release_phase": release_phase,
        "production_pipeline": "passed",
        "mihomo_matrix": "passed",
        "proof_status": "passed",
        "manifest_status": "passed",
        "release_status": release_status,
        "promotion_guard": promotion_guard,
        "warnings": [],
    }
    raw.update(overrides)
    return ProductionLifecycleResult.from_mapping(raw)


def test_push_dry_run_result_passes_event_audit() -> None:
    decision = resolve_publication_decision(event_name="push", explicit_publish=True)

    audit_production_event_result(_passed_result(), decision)


def test_enabled_schedule_published_result_passes_event_audit() -> None:
    decision = resolve_publication_decision(
        event_name="schedule",
        scheduled_publish="true",
    )
    result = _passed_result(
        publication_status="published",
        release_status="published",
        promotion_guard="passed",
    )

    audit_production_event_result(result, decision)


def test_dry_run_skip_remains_valid_for_missing_canonical_declarations() -> None:
    decision = resolve_publication_decision(
        event_name="schedule",
        scheduled_publish="false",
    )
    result = ProductionLifecycleResult.from_mapping(
        {
            "status": "skipped",
            "publication_status": "not_applicable",
            "reason": "canonical_declarations_missing",
        }
    )

    audit_production_event_result(result, decision)


def test_publish_decision_rejects_skipped_lifecycle_result() -> None:
    decision = resolve_publication_decision(
        event_name="schedule",
        scheduled_publish="true",
    )
    result = ProductionLifecycleResult.from_mapping(
        {
            "status": "skipped",
            "publication_status": "not_applicable",
            "reason": "canonical_declarations_missing",
        }
    )

    with pytest.raises(ValidationError, match="cannot resolve to a skipped"):
        audit_production_event_result(result, decision)


@pytest.mark.parametrize(
    "key",
    ["production_pipeline", "mihomo_matrix", "proof_status", "manifest_status"],
)
def test_event_audit_rejects_failed_core_release_evidence(key: str) -> None:
    decision = resolve_publication_decision(event_name="push")

    with pytest.raises(ValidationError, match=key):
        audit_production_event_result(
            _passed_result(**{key: "failed"}),
            decision,
        )


def test_event_audit_requires_verified_final_release_phase() -> None:
    decision = resolve_publication_decision(
        event_name="schedule",
        scheduled_publish="true",
    )
    result = _passed_result(
        publication_status="published",
        release_phase="published",
        release_status="published",
        promotion_guard="passed",
    )

    with pytest.raises(ValidationError, match="verified release phase"):
        audit_production_event_result(result, decision)


def test_publish_decision_rejects_dry_run_result() -> None:
    decision = resolve_publication_decision(
        event_name="schedule",
        scheduled_publish="true",
    )

    with pytest.raises(ValidationError, match="requires published lifecycle status"):
        audit_production_event_result(_passed_result(), decision)


@pytest.mark.parametrize(
    ("release_status", "promotion_guard", "message"),
    [
        ("dry-run", "passed", "release_status"),
        ("published", "skipped", "promotion_guard"),
    ],
)
def test_publish_decision_requires_commit_and_promotion_evidence(
    release_status: str,
    promotion_guard: str,
    message: str,
) -> None:
    decision = resolve_publication_decision(
        event_name="schedule",
        scheduled_publish="true",
    )
    result = _passed_result(
        publication_status="published",
        release_status=release_status,
        promotion_guard=promotion_guard,
    )

    with pytest.raises(ValidationError, match=message):
        audit_production_event_result(result, decision)


def test_dry_run_decision_rejects_published_result() -> None:
    decision = resolve_publication_decision(event_name="push")
    result = _passed_result(
        publication_status="published",
        release_status="published",
        promotion_guard="passed",
    )

    with pytest.raises(ValidationError, match="requires dry-run lifecycle status"):
        audit_production_event_result(result, decision)


@pytest.mark.parametrize(
    ("release_status", "promotion_guard", "message"),
    [
        ("published", "skipped", "release_status"),
        ("dry-run", "passed", "promotion_guard"),
    ],
)
def test_dry_run_decision_rejects_publication_side_effect_evidence(
    release_status: str,
    promotion_guard: str,
    message: str,
) -> None:
    decision = resolve_publication_decision(event_name="push")
    result = _passed_result(
        release_status=release_status,
        promotion_guard=promotion_guard,
    )

    with pytest.raises(ValidationError, match=message):
        audit_production_event_result(result, decision)
