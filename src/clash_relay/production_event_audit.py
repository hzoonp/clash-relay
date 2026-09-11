"""Fail-closed consistency audit between publication intent and lifecycle outcome."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .errors import ValidationError
from .production_lifecycle_result import (
    ProductionLifecycleResult,
    ProductionLifecycleStatus,
    ProductionPublicationStatus,
)
from .publication_decision import PublicationDecision
from .release_reliability import ReleasePhase


def _require_status(document: Mapping[str, Any], key: str, expected: str) -> None:
    if document.get(key) != expected:
        raise ValidationError(f"production event result requires {key}={expected!r}")


def audit_production_event_result(
    result: ProductionLifecycleResult,
    decision: PublicationDecision,
) -> None:
    """Reject contradictions between the canonical trigger decision and result.

    A dry-run may legitimately skip when canonical declarations are absent, which
    keeps public forks fail-closed. A publish decision, however, must finish as a
    verified published release. Optional post-release observability fields remain
    outside this gate because they may warn without invalidating an already valid
    release.
    """

    if result.status is ProductionLifecycleStatus.SKIPPED:
        if decision.should_publish:
            raise ValidationError(
                "production publish decision cannot resolve to a skipped lifecycle result"
            )
        return

    document = result.as_dict()
    for key in (
        "production_pipeline",
        "mihomo_matrix",
        "proof_status",
        "manifest_status",
    ):
        _require_status(document, key, "passed")

    if result.release_phase is not ReleasePhase.VERIFIED:
        raise ValidationError("production event result must finish in verified release phase")

    if decision.should_publish:
        if result.publication_status is not ProductionPublicationStatus.PUBLISHED:
            raise ValidationError(
                "production publish decision requires published lifecycle status"
            )
        _require_status(document, "release_status", "published")
        _require_status(document, "promotion_guard", "passed")
        return

    if result.publication_status is not ProductionPublicationStatus.DRY_RUN:
        raise ValidationError("production dry-run decision requires dry-run lifecycle status")
    _require_status(document, "release_status", "dry-run")
    _require_status(document, "promotion_guard", "skipped")
