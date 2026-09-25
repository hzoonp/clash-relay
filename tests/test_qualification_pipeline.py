from __future__ import annotations

import json
from pathlib import Path

import pytest

import clash_relay.qualification_pipeline as pipeline
from clash_relay.errors import ValidationError
from clash_relay.qualification_reliability import (
    QualificationFailureCategory,
    QualificationStageRejected,
)
from clash_relay.service_qualification import service_qualifications
from clash_relay.util import load_yaml_file


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


def test_pipeline_reports_reachability_in_three_separate_authorities(
    tmp_path: Path, monkeypatch
) -> None:
    candidate, policies, mihomo = _pipeline_inputs(tmp_path)
    _success_services(monkeypatch)

    result = pipeline.run_qualification_pipeline(
        candidate=candidate,
        output=tmp_path / "final.yaml",
        policies=policies,
        mihomo_bin=mihomo,
        stage_dir=tmp_path / "stages",
        browsing_report=tmp_path / "browsing.json",
        ai_report=tmp_path / "ai.json",
    )

    reachability = result["reachability"]
    assert set(reachability) == {
        "global_preflight_reachable",
        "client_runtime_health",
        "carrier_qualification",
    }
    # The runner-side endpoint admission and the client runtime contract stay
    # distinct: preflight results must never be read as carrier quality.
    assert reachability["global_preflight_reachable"] == result["endpoint_qualification"]
    client_health = reachability["client_runtime_health"]
    assert client_health["authority"] == "client_local_urltest"
    assert client_health["probe_url"] == "https://cp.cloudflare.com/generate_204"
    assert client_health["max_failed_times"] == {"browsing": 1, "regional_and_other": 2}
    assert client_health["browsing"] == result["browsing"]
    assert client_health["ai_status"] == "qualified"
    assert (
        client_health["accelerated_health_check_groups"]
        == (result["accelerated_health_check_groups"])
    )
    carrier = reachability["carrier_qualification"]
    assert carrier["status"] == "not_configured"
    assert carrier["carriers"] == {}


def test_browsing_failover_threshold_survives_later_qualification_stages(
    tmp_path: Path, monkeypatch
) -> None:
    candidate, policies, mihomo = _pipeline_inputs(tmp_path)
    candidate.write_text(
        "proxy-providers: {}\nproxies: []\nproxy-groups:\n"
        "  - {name: '网页 · 日本', type: fallback, url: 'https://example.invalid/204'}\n"
        "  - {name: '__CR_BROWSING_JP_STABLE_AUTO', type: url-test, url: 'https://example.invalid/204'}\n"
        "  - {name: '自动选择', type: url-test, url: 'https://example.invalid/204'}\n"
        "  - {name: 'AI · 日本', type: url-test, url: 'https://example.invalid/204'}\n",
        encoding="utf-8",
    )
    _success_services(monkeypatch)

    result = pipeline.run_qualification_pipeline(
        candidate=candidate,
        output=tmp_path / "final.yaml",
        policies=policies,
        mihomo_bin=mihomo,
        stage_dir=tmp_path / "stages",
        browsing_report=tmp_path / "browsing.json",
        ai_report=tmp_path / "ai.json",
    )
    groups = {
        group["name"]: group for group in load_yaml_file(tmp_path / "final.yaml")["proxy-groups"]
    }

    assert groups["网页 · 日本"]["max-failed-times"] == 1
    assert groups["__CR_BROWSING_JP_STABLE_AUTO"]["max-failed-times"] == 1
    assert groups["自动选择"]["max-failed-times"] == 2
    assert "max-failed-times" not in groups["AI · 日本"]
    assert result["accelerated_health_check_groups"] == 3


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


def _preflight_stubs(monkeypatch, *, drop_source: str | None = None):
    """Stub runner preflight stages with canned aggregate reports."""

    def host(document):
        if drop_source:
            for provider in document["proxy-providers"].values():
                provider["payload"] = [
                    proxy
                    for proxy in provider["payload"]
                    if f"{drop_source}/" not in str(proxy.get("name", ""))
                ]
        return {
            "status": "passed",
            "hostname_nodes": 2,
            "ip_literal_nodes": 0,
            "resolved": 0 if drop_source else 2,
            "unresolved": 2 if drop_source else 0,
            "dns_inconclusive": 0,
            "resolver_disagreement": 0,
            "quarantined": 2 if drop_source else 0,
            "by_source": {drop_source: 2} if drop_source else {},
            "by_region": {"jp": 2} if drop_source else {},
            "by_protocol": {"trojan": 2} if drop_source else {},
            "by_failure_category": {"nxdomain": 2} if drop_source else {},
            "by_source_failure_category": ({drop_source: {"nxdomain": 2}} if drop_source else {}),
        }

    def endpoint(document, workers=12):
        if drop_source:
            for provider in document["proxy-providers"].values():
                provider["payload"] = [
                    proxy
                    for proxy in provider["payload"]
                    if f"{drop_source}/" not in str(proxy.get("name", ""))
                ]
        return {
            "status": "passed",
            "tcp_nodes": 1,
            "tested": 1,
            "reachable": 0 if drop_source else 1,
            "unreachable": 1 if drop_source else 0,
            "quarantined": 1 if drop_source else 0,
            "skipped_udp_native": 0,
            "attempts": 3,
            "admission_quorum": 1,
            "robust_endpoints": 0 if drop_source else 1,
            "reserve_endpoints": 0,
            "by_source": {drop_source: 1} if drop_source else {},
            "by_region": {"jp": 1} if drop_source else {},
            "by_protocol": {"vless": 1} if drop_source else {},
            "by_failure_category": {"connect_timeout": 1} if drop_source else {},
            "by_source_failure_category": (
                {drop_source: {"connect_timeout": 1}} if drop_source else {}
            ),
        }

    monkeypatch.setattr(pipeline, "quarantine_unresolvable_proxy_hosts", host)
    monkeypatch.setattr(pipeline, "quarantine_unreachable_tcp_endpoints", endpoint)


def _candidate_with_source_nodes(tmp_path: Path) -> Path:
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text(
        "proxy-providers:\n"
        "  cr_browsing_jp:\n"
        "    payload:\n"
        "      - {name: '[BROWSING:JP] sub_2/One', type: trojan, server: a.example, port: 443}\n"
        "      - {name: '[BROWSING:JP] sub_2/Two', type: vless, server: b.example, port: 443}\n"
        "      - {name: '[BROWSING:JP] sub_3/One', type: trojan, server: c.example, port: 443}\n"
        "proxies: []\n"
        "proxy-groups: []\n",
        encoding="utf-8",
    )
    return candidate


def test_pipeline_reports_node_quality_tiers(tmp_path: Path, monkeypatch) -> None:
    candidate, policies, mihomo = _pipeline_inputs(tmp_path)
    _preflight_stubs(monkeypatch)
    _success_services_tail(monkeypatch)

    def browsing(**kwargs):
        _append(kwargs["candidate"], "browsing_stage")
        return {
            "status": "qualified",
            "automatic_nodes": 3,
            "stable_nodes": 2,
            "reserve_nodes": 1,
            "diagnostics": {"tested_nodes": 3, "qualified_nodes": 3, "failed_nodes": 1},
        }

    monkeypatch.setattr(pipeline, "run_browsing_qualification", browsing)

    result = pipeline.run_qualification_pipeline(
        candidate=candidate,
        output=tmp_path / "final.yaml",
        policies=policies,
        mihomo_bin=mihomo,
        stage_dir=tmp_path / "stages",
        browsing_report=tmp_path / "browsing.json",
        ai_report=tmp_path / "ai.json",
    )

    tiers = result["node_quality_tiers"]
    assert tiers["authority"] == "runner_preflight_evidence_plus_client_urltest"
    assert tiers["runner_endpoint_evidence"] == {"robust": 1, "reserve": 0, "quarantined": 0}
    assert tiers["runner_hostname_evidence"] == {"robust": 2, "reserve": 0, "quarantined": 0}
    assert tiers["browsing_node_evidence"] == {"robust": 2, "reserve": 1, "quarantined": 1}


def test_pipeline_flags_fully_removed_source_with_reasons(tmp_path: Path, monkeypatch) -> None:
    _, policies, mihomo = _pipeline_inputs(tmp_path)
    candidate = _candidate_with_source_nodes(tmp_path)
    _preflight_stubs(monkeypatch, drop_source="sub_2")
    _success_services_tail(monkeypatch)

    def browsing(**kwargs):
        _append(kwargs["candidate"], "browsing_stage")
        return {"status": "qualified", "automatic_nodes": 1}

    monkeypatch.setattr(pipeline, "run_browsing_qualification", browsing)

    result = pipeline.run_qualification_pipeline(
        candidate=candidate,
        output=tmp_path / "final.yaml",
        policies=policies,
        mihomo_bin=mihomo,
        stage_dir=tmp_path / "stages",
        browsing_report=tmp_path / "browsing.json",
        ai_report=tmp_path / "ai.json",
    )

    removed = result["removed_nodes"]
    assert removed["by_source"] == {"sub_2": 3}
    assert removed["by_failure_category"] == {"connect_timeout": 1, "nxdomain": 2}
    assert removed["sources_fully_removed"] == [
        {
            "source": "sub_2",
            "generated_nodes": 2,
            "final_nodes": 0,
            "by_failure_category": {"connect_timeout": 1, "nxdomain": 2},
        }
    ]
    # A partially surviving source is never flagged.
    assert "sub_3" not in {row["source"] for row in removed["sources_fully_removed"]}


def test_pipeline_consumes_self_hosted_carrier_payload(tmp_path: Path, monkeypatch) -> None:
    candidate, policies, mihomo = _pipeline_inputs(tmp_path)
    _success_services(monkeypatch)
    carrier_input = tmp_path / "carrier-qualification.json"
    carrier_input.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "carriers": {
                    "telecom": {"tested": 40, "reachable": 38, "median_latency_ms": 52.4},
                    "unicom": {"tested": 40, "reachable": 35, "median_latency_ms": 61.0},
                },
            }
        ),
        encoding="utf-8",
    )

    result = pipeline.run_qualification_pipeline(
        candidate=candidate,
        output=tmp_path / "final.yaml",
        policies=policies,
        mihomo_bin=mihomo,
        stage_dir=tmp_path / "stages",
        browsing_report=tmp_path / "browsing.json",
        ai_report=tmp_path / "ai.json",
        carrier_input=carrier_input,
    )

    carrier = result["reachability"]["carrier_qualification"]
    assert carrier["status"] == "passed"
    assert set(carrier["carriers"]) == {"telecom", "unicom"}
    assert carrier["aggregate"]["tested"] == 80


def test_pipeline_carrier_input_fails_closed_on_invalid_payload(
    tmp_path: Path, monkeypatch
) -> None:
    candidate, policies, mihomo = _pipeline_inputs(tmp_path)
    _success_services(monkeypatch)
    carrier_input = tmp_path / "carrier-qualification.json"
    carrier_input.write_text(
        json.dumps({"schema_version": 1, "carriers": {}, "endpoints": ["1.2.3.4"]}),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="unknown fields"):
        pipeline.run_qualification_pipeline(
            candidate=candidate,
            output=tmp_path / "final.yaml",
            policies=policies,
            mihomo_bin=mihomo,
            stage_dir=tmp_path / "stages",
            browsing_report=tmp_path / "browsing.json",
            ai_report=tmp_path / "ai.json",
            carrier_input=carrier_input,
        )
