"""Stable privacy-safe categories for failed production attempts.

Diagnostics intentionally classify typed failures without returning exception
messages, node identities, subscription URLs, probe endpoints, or credentials.
The categories are observability only: they never alter retry or fail-closed
behavior.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from .errors import (
    CandidateValidationStageError,
    CommitUnknownError,
    ConfigurationError,
    FetchError,
    GenerationError,
    PublicationError,
    SecretError,
    SubscriptionError,
    UnsafeSubscriptionError,
    ValidationError,
)
from .qualification_reliability import QualificationStageRejected


class ProductionFailureCategory(StrEnum):
    CONFIGURATION = "configuration"
    SUBSCRIPTION_FETCH = "subscription_fetch"
    SUBSCRIPTION_ADMISSION = "subscription_admission"
    SUBSCRIPTION_PARSE = "subscription_parse"
    GENERATION = "generation"
    BROWSING_QUALIFICATION = "browsing_qualification"
    AI_QUALIFICATION = "ai_qualification"
    QUALIFICATION = "qualification"
    CLOUDFLARE_PUBLICATION = "cloudflare_publication"
    CLOUDFLARE_COMMIT_UNKNOWN = "cloudflare_commit_unknown"
    CANDIDATE_VALIDATION = "candidate_validation"
    IO_FAILURE = "io_failure"
    UNKNOWN = "unknown"


_SAFE_QUALIFICATION_STAGES = frozenset(
    {
        "setup",
        "browsing",
        "history",
        "browsing_rewrite",
        "transport",
        "ai",
        "ai_service",
        "service",
    }
)
_SAFE_CANDIDATE_VALIDATION_STAGES = frozenset(
    {
        "production_pre_audit",
        "qualification_pipeline",
        "production_post_audit",
        "ai_setup",
        "ai_cache_fingerprints",
        "ai_service_probe",
        "ai_service_rewrite",
        "ai_route_postprocess",
        "release_baseline",
        "promotion_guard",
        "mihomo_matrix",
    }
)


def _chain(error: BaseException) -> tuple[BaseException, ...]:
    current: BaseException | None = error
    seen: set[int] = set()
    values: list[BaseException] = []
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        values.append(current)
        current = current.__cause__ or current.__context__
    return tuple(values)


def _qualification_category(error: QualificationStageRejected) -> ProductionFailureCategory:
    stage = error.stage.casefold()
    if "brows" in stage or "transport" in stage:
        return ProductionFailureCategory.BROWSING_QUALIFICATION
    if "ai" in stage or "service" in stage:
        return ProductionFailureCategory.AI_QUALIFICATION
    return ProductionFailureCategory.QUALIFICATION


def _safe_qualification_stage(stage: str) -> str:
    normalized = stage.strip().casefold()
    return normalized if normalized in _SAFE_QUALIFICATION_STAGES else "other"


def _safe_candidate_validation_stage(stage: str) -> str:
    normalized = stage.strip().casefold()
    return normalized if normalized in _SAFE_CANDIDATE_VALIDATION_STAGES else "other"


def _validation_stage(error: BaseException) -> str | None:
    if isinstance(error, CandidateValidationStageError):
        return error.stage
    if isinstance(error, ValidationError):
        stage = getattr(error, "validation_stage", None)
        if isinstance(stage, str):
            return stage
    return None


def safe_failure_diagnostic(error: BaseException) -> dict[str, Any]:
    """Classify one failure without copying any exception text into output."""

    chain = _chain(error)
    qualification = next(
        (item for item in chain if isinstance(item, QualificationStageRejected)),
        None,
    )
    if isinstance(qualification, QualificationStageRejected):
        return {
            "status": "failed",
            "category": _qualification_category(qualification).value,
            "qualification_stage": _safe_qualification_stage(qualification.stage),
            "qualification_failure_category": qualification.category.value,
            "retryable": qualification.retryable,
        }

    commit_unknown = next(
        (item for item in chain if isinstance(item, CommitUnknownError)),
        None,
    )
    if isinstance(commit_unknown, CommitUnknownError):
        return {
            "status": "failed",
            "category": ProductionFailureCategory.CLOUDFLARE_COMMIT_UNKNOWN.value,
            "production_changed": commit_unknown.production_changed,
        }

    for item in chain:
        stage = _validation_stage(item)
        if stage is not None:
            return {
                "status": "failed",
                "category": ProductionFailureCategory.CANDIDATE_VALIDATION.value,
                "validation_stage": _safe_candidate_validation_stage(stage),
            }

    category = ProductionFailureCategory.UNKNOWN
    if any(isinstance(item, SecretError | ConfigurationError) for item in chain):
        category = ProductionFailureCategory.CONFIGURATION
    elif any(isinstance(item, FetchError) for item in chain):
        category = ProductionFailureCategory.SUBSCRIPTION_FETCH
    elif any(isinstance(item, UnsafeSubscriptionError) for item in chain):
        category = ProductionFailureCategory.SUBSCRIPTION_ADMISSION
    elif any(isinstance(item, SubscriptionError) for item in chain):
        category = ProductionFailureCategory.SUBSCRIPTION_PARSE
    elif any(isinstance(item, GenerationError) for item in chain):
        category = ProductionFailureCategory.GENERATION
    elif any(isinstance(item, PublicationError) for item in chain):
        category = ProductionFailureCategory.CLOUDFLARE_PUBLICATION
    elif any(isinstance(item, ValidationError) for item in chain):
        category = ProductionFailureCategory.CANDIDATE_VALIDATION
    elif any(isinstance(item, OSError) for item in chain):
        category = ProductionFailureCategory.IO_FAILURE

    return {"status": "failed", "category": category.value}
