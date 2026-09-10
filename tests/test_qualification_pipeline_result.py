from __future__ import annotations

import pytest

from clash_relay.errors import ValidationError
from clash_relay.qualification_pipeline_result import QualificationPipelineResult

_STAGE_NAMES = (
    "generated",
    "browsing_transport_qualified",
    "ai_qualified",
    "service_client_path_hardened",
    "final_qualified",
)


def _valid_result() -> dict[str, object]:
    return {
        "status": "qualified",
        "policy_model_version": 2,
        "stages": [
            {"name": name, "fingerprint": str(index) * 64}
            for index, name in enumerate(_STAGE_NAMES, start=1)
        ],
        "timings_ms": {
            "browsing_transport": 10.5,
            "ai": 20,
            "service_client_path": 1.25,
            "total": 35.0,
        },
        "browsing": {
            "status": "qualified",
            "automatic_nodes": 3,
            "stage_attempts": 1,
            "recovered_by_retry": False,
            "recovered_failure_category": None,
        },
        "ai": {
            "status": "qualified",
            "qualification_mode": "per-service",
            "services": {"OpenAI": {"status": "qualified"}},
            "client_path_status": "passed",
            "client_path_hardened_services": 3,
            "client_path_services": ["Claude", "Gemini", "OpenAI"],
        },
    }


def test_result_accepts_canonical_contract_and_preserves_extensions() -> None:
    document = _valid_result()
    document["future_observability"] = {"safe": True}

    result = QualificationPipelineResult.from_mapping(document)

    assert result.status.value == "qualified"
    assert result.policy_model_version == 2
    assert [stage.name for stage in result.stages] == list(_STAGE_NAMES)
    assert result.as_dict() == document


def test_result_accepts_typed_transient_retry_recovery() -> None:
    document = _valid_result()
    browsing = document["browsing"]
    assert isinstance(browsing, dict)
    browsing["stage_attempts"] = 2
    browsing["recovered_by_retry"] = True
    browsing["recovered_failure_category"] = "transient"

    result = QualificationPipelineResult.from_mapping(document)

    assert result.browsing_stage_attempts == 2
    assert result.browsing_recovered_by_retry is True
    assert result.browsing_recovered_failure_category == "transient"


def test_result_rejects_stage_order_drift() -> None:
    document = _valid_result()
    stages = document["stages"]
    assert isinstance(stages, list)
    stages[1], stages[2] = stages[2], stages[1]

    with pytest.raises(ValidationError, match="canonical order"):
        QualificationPipelineResult.from_mapping(document)


def test_result_rejects_invalid_stage_fingerprint() -> None:
    document = _valid_result()
    stages = document["stages"]
    assert isinstance(stages, list)
    first = stages[0]
    assert isinstance(first, dict)
    first["fingerprint"] = "not-a-sha"

    with pytest.raises(ValidationError, match="fingerprint"):
        QualificationPipelineResult.from_mapping(document)


def test_result_rejects_inconsistent_retry_evidence() -> None:
    document = _valid_result()
    browsing = document["browsing"]
    assert isinstance(browsing, dict)
    browsing["recovered_by_retry"] = True
    browsing["recovered_failure_category"] = "transient"

    with pytest.raises(ValidationError, match="second attempt"):
        QualificationPipelineResult.from_mapping(document)


def test_result_rejects_invalid_ai_client_path_status() -> None:
    document = _valid_result()
    ai = document["ai"]
    assert isinstance(ai, dict)
    ai["client_path_status"] = "unknown"

    with pytest.raises(ValidationError, match="client path hardening"):
        QualificationPipelineResult.from_mapping(document)


def test_result_rejects_negative_timing() -> None:
    document = _valid_result()
    timings = document["timings_ms"]
    assert isinstance(timings, dict)
    timings["ai"] = -1

    with pytest.raises(ValidationError, match=r"timings_ms\.ai"):
        QualificationPipelineResult.from_mapping(document)
