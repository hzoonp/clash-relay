"""Fail-closed consistency audit between publication intent and lifecycle outcome."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .errors import ValidationError
from .production_lifecycle_result import (
    ProductionLifecycleResult,
    ProductionLifecycleStatus,
    ProductionPublicationStatus,
    ProductionReleaseStatus,
)
from .publication_decision import PublicationDecision
from .release_reliability import ReleasePhase


def _require_status(document: Mapping[str, Any], key: str, expected: str) -> None:
    if document.get(key) != expected:
        raise ValidationError(f"production event result requires {key}={expected!r}")


def _audit_published_observability(
    result: ProductionLifecycleResult,
    document: Mapping[str, Any],
) -> None:
    """Validate post-commit evidence without rewriting a committed release as failed."""

    proof_status = document.get("proof_status")
    manifest_status = document.get("manifest_status")
    allowed = {"passed", "unavailable"}
    if proof_status not in allowed:
        raise ValidationError(
            "published production event requires proof_status='passed' or 'unavailable'"
        )
    if manifest_status not in allowed:
        raise ValidationError(
            "published production event requires manifest_status='passed' or 'unavailable'"
        )

    degraded = proof_status != "passed" or manifest_status != "passed"
    if not degraded:
        if result.release_phase is not ReleasePhase.VERIFIED:
            raise ValidationError(
                "published production event with complete observability must finish verified"
            )
        return

    if result.release_phase is not ReleasePhase.PUBLISHED:
        raise ValidationError(
            "published production event with degraded observability must remain in published phase"
        )

    warnings = set(result.warnings)
    if proof_status == "unavailable" and "render_production_proof" not in warnings:
        raise ValidationError(
            "unavailable production proof requires render_production_proof warning evidence"
        )
    if manifest_status == "unavailable" and "render_release_manifest" not in warnings:
        raise ValidationError(
            "unavailable release manifest requires render_release_manifest warning evidence"
        )


def audit_production_event_result(
    result: ProductionLifecycleResult,
    decision: PublicationDecision,
) -> None:
    """Reject contradictions between the canonical trigger decision and result.

    A dry-run may legitimately skip when canonical declarations are absent, which
    keeps public forks fail-closed. A publish decision must prove its pre-commit
    safety gates and either finish fully verified or remain explicitly published
    with matching post-commit observability warnings. An idempotent release
    transaction may report ``unchanged`` after proving that the exact candidate
    bytes are already live.
    """

    if result.status is ProductionLifecycleStatus.SKIPPED:
        if decision.should_publish:
            raise ValidationError(
                "production publish decision cannot resolve to a skipped lifecycle result"
            )
        return

    document = result.as_dict()
    for key in ("production_pipeline", "mihomo_matrix"):
        _require_status(document, key, "passed")

    if decision.should_publish:
        if result.publication_status is not ProductionPublicationStatus.PUBLISHED:
            raise ValidationError("production publish decision requires published lifecycle status")
        if result.release_status not in {
            ProductionReleaseStatus.PUBLISHED,
            ProductionReleaseStatus.UNCHANGED,
        }:
            raise ValidationError(
                "production publish decision requires release_status='published' or 'unchanged'"
            )
        _require_status(document, "promotion_guard", "passed")
        _audit_published_observability(result, document)
        return

    for key in ("proof_status", "manifest_status"):
        _require_status(document, key, "passed")
    if result.release_phase is not ReleasePhase.VERIFIED:
        raise ValidationError("production dry-run result must finish in verified release phase")
    if result.publication_status is not ProductionPublicationStatus.DRY_RUN:
        raise ValidationError("production dry-run decision requires dry-run lifecycle status")
    if result.release_status is not ProductionReleaseStatus.DRY_RUN:
        raise ValidationError("production dry-run decision requires release_status='dry-run'")
    _require_status(document, "promotion_guard", "skipped")
