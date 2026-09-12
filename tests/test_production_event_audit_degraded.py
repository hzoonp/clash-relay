from __future__ import annotations

import pytest

from clash_relay.errors import ValidationError
from clash_relay.production_event_audit import audit_production_event_result
from clash_relay.production_lifecycle_result import ProductionLifecycleResult
from clash_relay.publication_decision import resolve_publication_decision


def _published_degraded_result(
    *,
    release_phase: str = "published",
    proof_status: str = "passed",
    manifest_status: str = "passed",
    warnings: list[str] | None = None,
) -> ProductionLifecycleResult:
    return ProductionLifecycleResult.from_mapping(
        {
            "status": "passed",
            "publication_status": "published",
            "release_phase": release_phase,
            "production_pipeline": "passed",
            "mihomo_matrix": "passed",
            "proof_status": proof_status,
            "manifest_status": manifest_status,
            "release_status": "published",
            "promotion_guard": "passed",
            "warnings": warnings or [],
        }
    )


def _publish_decision():
    return resolve_publication_decision(event_name="schedule", scheduled_publish="true")


def test_degraded_observability_cannot_claim_verified_phase() -> None:
    result = _published_degraded_result(
        release_phase="verified",
        proof_status="unavailable",
        warnings=["render_production_proof"],
    )

    with pytest.raises(ValidationError, match="must remain in published phase"):
        audit_production_event_result(result, _publish_decision())


def test_unavailable_manifest_requires_matching_warning_evidence() -> None:
    result = _published_degraded_result(manifest_status="unavailable")

    with pytest.raises(ValidationError, match="render_release_manifest warning"):
        audit_production_event_result(result, _publish_decision())
