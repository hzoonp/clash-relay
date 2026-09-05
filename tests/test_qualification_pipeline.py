from __future__ import annotations

from pathlib import Path

import pytest

import clash_relay.qualification_pipeline as pipeline
from clash_relay.errors import ValidationError
from clash_relay.qualification_reliability import (
    QualificationFailureCategory,
    QualificationStageRejected,
)
from clash_relay.service_qualification import service_qualifications


def _pipeline_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text("proxy-groups: []\nproxy-providers: {}\nproxies: []\n", encoding="utf-8")
    policies = Path(__file__).resolve().parent / "fixtures/project/policies.yaml"
    mihomo = tmp_path / "mihomo"
    mihomo.write_text("fake", encoding="utf-8")
    return candidate, policies, mihomo


def _append(path: Path, marker: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n{marker}: true\n")


def _ai_summary() -> dict[str, object]:
    return {
        "status": "qualified",
        "diagnostics": {
            "qualification_mode": "per-service",
            "probes": {
                service.probe_name: {
                    "live_tested_nodes": 1,
                    "cache_pass_hits": 0,
                    "cache_fail_hits": 0,
                    "qualified_nodes": 1,
                    "outcomes": {"passed": 1},
                }
                for service in service_qualifications()
            },
        },
    }


def _success_services(monkeypatch) -> None:
    def browsing(**kwargs):
        _append(kwargs["candidate"], "browsing_stage")
        return {"status": "qualified", "automatic_nodes": 3}

    def ai(**kwargs):
        _append(kwargs["candidate"], "ai_stage")
        return _ai_summary()

    def service_paths(*, candidate, policies):
        assert policies.name == "policies.yaml"
        _append(candidate, "service_runtime_stage")
        return {"status": "passed", "hardened_services": 1, "services": {"example": {}}}

    monkeypatch.setattr(pipeline, "run_browsing_qualification", browsing)
    monkeypatch.setattr(pipeline, "run_ai_qualification", ai)
    monkeypatch.setattr(pipeline, "harden_declared_service_client_paths", service_paths)


def test_pipeline_uses_private_sequential_stage_files(tmp_path: Path, monkeypatch) -> None:
    candidate, policies, mihomo = _pipeline_inputs(tmp_path)
    _success_services(monkeypatch)

    output = tmp_path / "final.yaml"
    browsing_report = tmp_path / "browsing.json"
    ai_report = tmp_path / "ai.json"
    result = pipeline.run_qualification_pipeline(
        candidate=candidate,
        output=output,
        policies=policies,
        mihomo_bin=mihomo,
        stage_dir=tmp_path / "stages",
        browsing_report=browsing_report,
        ai_report=ai_report,
    )

    text = output.read_text(encoding="utf-8")
    assert "browsing_stage: true" in text
    assert "ai_stage: true" in text
    assert "service_runtime_stage: true" in text
    assert "browsing_stage" not in candidate.read_text(encoding="utf-8")
    assert result["status"] == "qualified"
    assert result["policy_model_version"] == 2
    assert result["browsing"]["stage_attempts"] == 1
    assert result["browsing"]["recovered_by_retry"] is False
    assert result["browsing"]["recovered_failure_category"] is None
    assert [row["name"] for row in result["stages"]] == [
        "generated",
        "browsing_transport_qualified",
        "ai_qualified",
        "service_client_path_hardened",
        "final_qualified",
    ]
    assert result["ai"]["client_path_status"] == "passed"
    assert result["ai"]["client_path_hardened_services"] == 1
    assert result["ai"]["client_path_services"] == ["example"]
    assert set(result["ai"]["services"]) == {service.label for service in service_qualifications()}
    assert browsing_report.exists()
    assert ai_report.exists()


def test_pipeline_retries_only_typed_transient_from_immutable_candidate(
    tmp_path: Path, monkeypatch
) -> None:
    candidate, policies, mihomo = _pipeline_inputs(tmp_path)
    calls = 0

    def browsing(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            _append(kwargs["candidate"], "failed_attempt_marker")
            raise QualificationStageRejected(
                stage="browsing",
                category=QualificationFailureCategory.TRANSIENT,
                retryable=True,
                diagnostics={
                    "tested_nodes": 4,
                    "qualified_nodes": 0,
                    "successful_samples": 0,
                    "failed_samples": 12,
                    "outcomes": {"probe_error": 12},
                },
            )
        _append(kwargs["candidate"], "browsing_stage")
        return {"status": "qualified", "automatic_nodes": 3}

    monkeypatch.setattr(pipeline, "run_browsing_qualification", browsing)
    monkeypatch.setattr(pipeline.time, "sleep", lambda _seconds: None)
    _success_services_tail(monkeypatch)

    output = tmp_path / "final.yaml"
    result = pipeline.run_qualification_pipeline(
        candidate=candidate,
        output=output,
        policies=policies,
        mihomo_bin=mihomo,
        stage_dir=tmp_path / "stages",
        browsing_report=tmp_path / "browsing.json",
        ai_report=tmp_path / "ai.json",
    )

    assert calls == 2
    assert result["browsing"]["stage_attempts"] == 2
    assert result["browsing"]["recovered_by_retry"] is True
    assert result["browsing"]["recovered_failure_category"] == "transient"
    text = output.read_text(encoding="utf-8")
    assert "browsing_stage: true" in text
    assert "failed_attempt_marker" not in text


def _success_services_tail(monkeypatch) -> None:
    def ai(**kwargs):
        _append(kwargs["candidate"], "ai_stage")
        return _ai_summary()

    def service_paths(*, candidate, policies):
        assert policies.name == "policies.yaml"
        _append(candidate, "service_runtime_stage")
        return {"status": "passed", "hardened_services": 1, "services": {"example": {}}}

    monkeypatch.setattr(pipeline, "run_ai_qualification", ai)
    monkeypatch.setattr(pipeline, "harden_declared_service_client_paths", service_paths)


def test_pipeline_does_not_retry_policy_rejection(tmp_path: Path, monkeypatch) -> None:
    candidate, policies, mihomo = _pipeline_inputs(tmp_path)
    calls = 0

    def browsing(**_kwargs):
        nonlocal calls
        calls += 1
        raise QualificationStageRejected(
            stage="transport",
            category=QualificationFailureCategory.POLICY_REJECTION,
            retryable=False,
            transport_diagnostics={
                "tested_nodes": 8,
                "tcp_qualified_nodes": 8,
                "udp_qualified_nodes": 0,
            },
        )

    monkeypatch.setattr(pipeline, "run_browsing_qualification", browsing)

    with pytest.raises(ValidationError, match="policy_rejection"):
        pipeline.run_qualification_pipeline(
            candidate=candidate,
            output=tmp_path / "final.yaml",
            policies=policies,
            mihomo_bin=mihomo,
            stage_dir=tmp_path / "stages",
            browsing_report=tmp_path / "browsing.json",
            ai_report=tmp_path / "ai.json",
        )

    assert calls == 1


def test_pipeline_does_not_retry_unexpected_internal_validation_error(
    tmp_path: Path, monkeypatch
) -> None:
    candidate, policies, mihomo = _pipeline_inputs(tmp_path)
    calls = 0

    def browsing(**_kwargs):
        nonlocal calls
        calls += 1
        raise ValidationError("internal contract failure")

    monkeypatch.setattr(pipeline, "run_browsing_qualification", browsing)

    with pytest.raises(ValidationError, match="internal contract failure"):
        pipeline.run_qualification_pipeline(
            candidate=candidate,
            output=tmp_path / "final.yaml",
            policies=policies,
            mihomo_bin=mihomo,
            stage_dir=tmp_path / "stages",
            browsing_report=tmp_path / "browsing.json",
            ai_report=tmp_path / "ai.json",
        )

    assert calls == 1


def test_pipeline_surfaces_only_aggregate_rejection_diagnostics(
    tmp_path: Path, monkeypatch
) -> None:
    candidate, policies, mihomo = _pipeline_inputs(tmp_path)

    def browsing(**_kwargs):
        raise QualificationStageRejected(
            stage="transport",
            category=QualificationFailureCategory.POLICY_REJECTION,
            retryable=False,
            diagnostics={
                "tested_nodes": 7,
                "qualified_nodes": 5,
                "outcomes": {"success": 15},
                "server": "private.example",
            },
            transport_diagnostics={
                "tested_nodes": 8,
                "tcp_qualified_nodes": 8,
                "udp_qualified_nodes": 0,
                "selector_failures": 0,
                "token": "top-secret",
            },
        )

    monkeypatch.setattr(pipeline, "run_browsing_qualification", browsing)

    with pytest.raises(ValidationError) as caught:
        pipeline.run_qualification_pipeline(
            candidate=candidate,
            output=tmp_path / "final.yaml",
            policies=policies,
            mihomo_bin=mihomo,
            stage_dir=tmp_path / "stages",
            browsing_report=tmp_path / "browsing.json",
            ai_report=tmp_path / "ai.json",
        )

    message = str(caught.value)
    assert "policy_rejection" in message
    assert '"stage":"transport"' in message
    assert '"tested_nodes":8' in message
    assert '"udp_qualified_nodes":0' in message
    assert "private.example" not in message
    assert "top-secret" not in message
