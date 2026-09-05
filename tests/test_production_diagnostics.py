from __future__ import annotations

from clash_relay.errors import (
    ConfigurationError,
    FetchError,
    GenerationError,
    PublicationError,
    SubscriptionError,
    UnsafeSubscriptionError,
    ValidationError,
)
from clash_relay.production_diagnostics import safe_failure_diagnostic
from clash_relay.qualification_reliability import (
    QualificationFailureCategory,
    QualificationStageRejected,
)


def test_typed_production_failures_map_to_stable_categories_without_messages() -> None:
    cases = [
        (ConfigurationError("secret.example/token"), "configuration"),
        (FetchError("https://secret.example/subscription"), "subscription_fetch"),
        (UnsafeSubscriptionError("server=10.0.0.1"), "subscription_admission"),
        (SubscriptionError("password=do-not-leak"), "subscription_parse"),
        (GenerationError("node-secret"), "generation"),
        (PublicationError("cloudflare token secret"), "cloudflare_publication"),
        (ValidationError("private node 192.0.2.1"), "candidate_validation"),
        (OSError("/private/secret/path"), "io_failure"),
    ]

    for error, expected in cases:
        result = safe_failure_diagnostic(error)
        assert result == {"status": "failed", "category": expected}
        encoded = repr(result)
        assert str(error) not in encoded


def test_qualification_failure_uses_typed_stage_and_category_only() -> None:
    rejection = QualificationStageRejected(
        stage="browsing",
        category=QualificationFailureCategory.TRANSIENT,
        retryable=True,
        diagnostics={"server": "secret.example", "token": "do-not-leak"},
    )
    wrapped = ValidationError("aggregate wrapper")
    wrapped.__cause__ = rejection

    result = safe_failure_diagnostic(wrapped)

    assert result == {
        "status": "failed",
        "category": "browsing_qualification",
        "qualification_failure_category": "transient",
        "retryable": True,
    }
    assert "secret.example" not in repr(result)
    assert "do-not-leak" not in repr(result)


def test_ai_and_unknown_qualification_stages_are_coarsened() -> None:
    ai = QualificationStageRejected(
        stage="ai_service",
        category=QualificationFailureCategory.POLICY_REJECTION,
        retryable=False,
    )
    other = QualificationStageRejected(
        stage="future_stage",
        category=QualificationFailureCategory.PROTOCOL_ERROR,
        retryable=False,
    )

    assert safe_failure_diagnostic(ai)["category"] == "ai_qualification"
    assert safe_failure_diagnostic(other)["category"] == "qualification"


def test_unknown_exception_never_serializes_exception_text() -> None:
    error = RuntimeError("credential=super-secret")
    assert safe_failure_diagnostic(error) == {"status": "failed", "category": "unknown"}
