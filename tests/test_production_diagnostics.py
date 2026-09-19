from __future__ import annotations

import pytest

from clash_relay.errors import (
    CandidateValidationStageError,
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
        "qualification_stage": "browsing",
        "qualification_failure_category": "transient",
        "retryable": True,
    }
    assert "secret.example" not in repr(result)
    assert "do-not-leak" not in repr(result)


def test_unknown_qualification_stage_is_not_reflected_verbatim() -> None:
    rejection = QualificationStageRejected(
        stage="private-node-name.example",
        category=QualificationFailureCategory.CONFIGURATION,
        retryable=False,
    )

    result = safe_failure_diagnostic(rejection)

    assert result["qualification_stage"] == "other"
    assert "private-node-name.example" not in repr(result)


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
    assert safe_failure_diagnostic(ai)["qualification_stage"] == "ai_service"
    assert safe_failure_diagnostic(other)["category"] == "qualification"
    assert safe_failure_diagnostic(other)["qualification_stage"] == "other"


def test_candidate_validation_stage_is_static_and_privacy_safe() -> None:
    error = CandidateValidationStageError("ai_service_rewrite")
    error.__cause__ = ValidationError("private-node.example token=secret")

    assert safe_failure_diagnostic(error) == {
        "status": "failed",
        "category": "candidate_validation",
        "validation_stage": "ai_service_rewrite",
    }
    assert "private-node.example" not in repr(safe_failure_diagnostic(error))
    assert "secret" not in repr(safe_failure_diagnostic(error))


@pytest.mark.parametrize(
    ("message", "expected_stage"),
    [
        (
            "AI qualification could not resolve every country provider route",
            "ai_service_routes",
        ),
        ("AI routing uses unknown preferred region 'PRIVATE'", "ai_service_country_order"),
        ("AI service qualification returned unknown candidate nodes", "ai_service_inputs"),
        (
            "no nodes passed all AI qualification probes; refusing to replace the published profile",
            "ai_service_union_prune",
        ),
        (
            "AI service fallback template is missing 'private-field'",
            "ai_service_group_build",
        ),
        (
            "pinned ACL4SSR AI rules changed; service routing requires review",
            "ai_service_rules",
        ),
        (
            "generated configuration is invalid: private-node.example token=secret",
            "ai_service_validate",
        ),
    ],
)
def test_ai_service_rewrite_known_errors_map_to_static_safe_substages(
    message: str,
    expected_stage: str,
) -> None:
    outer = CandidateValidationStageError("ai_service_rewrite")
    outer.__cause__ = ValidationError(message)

    result = safe_failure_diagnostic(outer)

    assert result == {
        "status": "failed",
        "category": "candidate_validation",
        "validation_stage": expected_stage,
    }
    assert message not in repr(result)
    assert "private" not in repr(result)
    assert "secret" not in repr(result)


def test_unknown_ai_service_rewrite_error_stays_generic_without_leaking_text() -> None:
    outer = CandidateValidationStageError("ai_service_rewrite")
    outer.__cause__ = ValidationError("private-node.example token=super-secret")

    result = safe_failure_diagnostic(outer)

    assert result == {
        "status": "failed",
        "category": "candidate_validation",
        "validation_stage": "ai_service_rewrite",
    }
    assert "private-node.example" not in repr(result)
    assert "super-secret" not in repr(result)


def test_tagged_validation_error_keeps_message_but_diagnostic_is_safe() -> None:
    error = ValidationError("promotion blocked for private-node.example")
    error.validation_stage = "promotion_guard"  # type: ignore[attr-defined]

    assert str(error) == "promotion blocked for private-node.example"
    assert safe_failure_diagnostic(error) == {
        "status": "failed",
        "category": "candidate_validation",
        "validation_stage": "promotion_guard",
    }
    assert "private-node.example" not in repr(safe_failure_diagnostic(error))


def test_promotion_guard_source_admission_diagnostic_is_aggregate_only() -> None:
    error = ValidationError("private promotion detail")
    error.validation_stage = "promotion_guard"  # type: ignore[attr-defined]
    error.source_admission_report = {  # type: ignore[attr-defined]
        "successful_subscriptions": 2,
        "parsed_nodes": 130,
        "usable_nodes": 130,
        "name_filtered_nodes": 3,
        "multiplier_filtered_nodes": 7,
        "subscriptions": [
            {
                "id": "subscription_1",
                "status": "failed",
                "nodes": 0,
                "filtered_by_name": 3,
                "filtered_over_multiplier": 7,
                "max_node_multiplier": 2.0,
                "failure_category": "subscription_fetch",
                "failure_reason": "dns_error",
                "empty_payload_shape": "remote_provider_only",
                "error": "https://private.example/token",
            },
            "invalid-row",
        ],
        "secret": "do-not-leak",
    }

    result = safe_failure_diagnostic(error)

    assert result["source_admission"] == {
        "successful_subscriptions": 2,
        "parsed_nodes": 130,
        "usable_nodes": 130,
        "name_filtered_nodes": 3,
        "multiplier_filtered_nodes": 7,
        "subscriptions": [
            {
                "id": "subscription_1",
                "status": "failed",
                "nodes": 0,
                "filtered_by_name": 3,
                "filtered_over_multiplier": 7,
                "max_node_multiplier": 2.0,
                "failure_category": "subscription_fetch",
                "failure_reason": "dns_error",
                "empty_payload_shape": "remote_provider_only",
            }
        ],
    }
    assert "private.example" not in repr(result)
    assert "do-not-leak" not in repr(result)


def test_promotion_guard_drops_unrecognized_source_failure_codes() -> None:
    error = ValidationError("private promotion detail")
    error.validation_stage = "promotion_guard"  # type: ignore[attr-defined]
    error.source_admission_report = {  # type: ignore[attr-defined]
        "subscriptions": [
            {
                "id": "subscription_1",
                "status": "failed",
                "failure_category": "private-category-token",
                "failure_reason": "https://private.example/token",
                "empty_payload_shape": "private-shape-token",
            }
        ]
    }

    result = safe_failure_diagnostic(error)

    assert result["source_admission"]["subscriptions"] == [
        {"id": "subscription_1", "status": "failed"}
    ]
    assert "private-category-token" not in repr(result)
    assert "private-shape-token" not in repr(result)
    assert "private.example" not in repr(result)


def test_promotion_guard_ignores_malformed_source_admission_diagnostic() -> None:
    error = ValidationError("private promotion detail")
    error.validation_stage = "promotion_guard"  # type: ignore[attr-defined]
    error.source_admission_report = "invalid"  # type: ignore[attr-defined]

    result = safe_failure_diagnostic(error)

    assert "source_admission" not in result


def test_unknown_candidate_validation_stage_is_not_reflected_verbatim() -> None:
    error = CandidateValidationStageError("private-node-name.example")

    result = safe_failure_diagnostic(error)

    assert result == {
        "status": "failed",
        "category": "candidate_validation",
        "validation_stage": "other",
    }
    assert "private-node-name.example" not in repr(result)


def test_unknown_exception_never_serializes_exception_text() -> None:
    error = RuntimeError("credential=super-secret")
    assert safe_failure_diagnostic(error) == {"status": "failed", "category": "unknown"}
