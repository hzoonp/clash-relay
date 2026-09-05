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
    CANDIDATE_VALIDATION = "candidate_validation"
    IO_FAILURE = "io_failure"
    UNKNOWN = "unknown"


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
            "qualification_failure_category": qualification.category.value,
            "retryable": qualification.retryable,
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
