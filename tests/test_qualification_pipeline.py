from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import clash_relay.qualification_pipeline as pipeline
from clash_relay.errors import ValidationError
from clash_relay.qualification_reliability import (
    QualificationFailureCategory,
    QualificationStageRejected,
)
from clash_relay.service_qualification import service_qualifications
from clash_relay.util import atomic_write, dump_yaml, load_yaml_file


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


def _preflight_stubs(monkeypatch, *, drop_source: str | None = None, host_passes: bool = False):
    """Stub runner preflight stages; aggregate reports derive from the actual
    payload removals so unique/runtime-entry accounting stays truthful."""

    def _prune(document):
        removed: list[tuple[str, dict]] = []
        if drop_source:
            for provider_name, provider in document["proxy-providers"].items():
                kept = []
                for proxy in provider["payload"]:
                    if f"{drop_source}/" in str(proxy.get("name", "")):
                        removed.append((provider_name, proxy))
                    else:
                        kept.append(proxy)
                provider["payload"] = kept
        return removed

    def _account(removed, *, category, region_for):
        source = pipeline._source(removed[0][1].get("name"))
        unique = {
            (
                pipeline._source(proxy.get("name")),
                str(proxy.get("server")),
                str(proxy.get("port")),
                str(proxy.get("type")),
            )
            for _, proxy in removed
        }
        return {
            "quarantined": len(removed),
            "unique_quarantined_nodes": len(unique),
            "by_source": {source: len(unique)},
            "by_region": {region_for: len(unique)},
            "by_failure_category": {category: len(unique)},
            "by_source_failure_category": {source: {category: len(unique)}},
        }

    def host(document):
        removed = _prune(document) if not host_passes else []
        report: dict = {
            "status": "passed",
            "hostname_nodes": len(removed),
            "ip_literal_nodes": 0,
            "resolved": 0,
            "unresolved": len(removed),
            "dns_inconclusive": 0,
            "resolver_disagreement": 0,
            "quarantined": len(removed),
        }
        if removed:
            report.update(_account(removed, category="nxdomain", region_for="jp"))
        else:
            report.update(
                {
                    "resolved": 2,
                    "unique_quarantined_nodes": 0,
                    "by_source": {},
                    "by_region": {},
                    "by_failure_category": {},
                    "by_source_failure_category": {},
                }
            )
        return report

    def endpoint(document, workers=12):
        removed = _prune(document) if host_passes else []
        report: dict = {
            "status": "passed",
            "tcp_nodes": len(removed) if removed else 1,
            "tested": len(removed) if removed else 1,
            "reachable": 0 if removed else 1,
            "unreachable": len(removed),
            "skipped_udp_native": 0,
            "attempts": 3,
            "admission_quorum": 1,
            "robust_endpoints": 0 if removed else 1,
            "reserve_endpoints": 0,
            "dns_inconclusive": 0,
            "quarantined": len(removed),
        }
        if removed:
            report.update(_account(removed, category="connect_timeout", region_for="jp"))
        else:
            report.update(
                {
                    "unique_quarantined_nodes": 0,
                    "by_source": {},
                    "by_region": {},
                    "by_failure_category": {},
                    "by_source_failure_category": {},
                }
            )
        return report

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
    assert removed["unique_nodes"] == 2
    assert removed["runtime_entries"] == 2
    assert removed["by_source"] == {"sub_2": 2}
    assert removed["by_failure_category"] == {"nxdomain": 2}
    assert removed["sources_fully_removed"] == [
        {
            "source": "sub_2",
            "unique_nodes": 2,
            "final_unique_nodes": 0,
            "runtime_entries": 2,
            "final_runtime_entries": 0,
            "removed_at_stage": "hostname",
            "by_stage": {"hostname": 2},
            "by_failure_category": {"nxdomain": 2},
        }
    ]
    # A partially surviving source is never flagged.
    assert "sub_3" not in {row["source"] for row in removed["sources_fully_removed"]}


def test_pipeline_sources_fully_removed_report_unique_and_runtime_entries(
    tmp_path: Path, monkeypatch
) -> None:
    """One physical node replicated into several runtime providers counts once
    as a unique node and once per provider as a runtime entry."""

    _, policies, mihomo = _pipeline_inputs(tmp_path)
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text(
        "proxy-providers:\n"
        "  cr_browsing_jp:\n"
        "    payload:\n"
        "      - {name: '[BROWSING:JP] sub_2/Solo', type: trojan, server: a.example, port: 443}\n"
        "      - {name: '[BROWSING:JP] sub_2/Ip', type: ss, server: 8.8.4.4, port: 443}\n"
        "  cr_general_jp:\n"
        "    payload:\n"
        "      - {name: '[GENERAL:ANY] sub_2/Solo', type: trojan, server: a.example, port: 443}\n"
        "      - {name: '[GENERAL:ANY] sub_2/Ip', type: ss, server: 8.8.4.4, port: 443}\n"
        "proxies: []\n"
        "proxy-groups: []\n",
        encoding="utf-8",
    )
    _preflight_stubs(monkeypatch, drop_source="sub_2", host_passes=True)
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
    assert removed["runtime_entries"] == 4
    assert removed["unique_nodes"] == 2
    assert removed["sources_fully_removed"] == [
        {
            "source": "sub_2",
            "unique_nodes": 2,
            "final_unique_nodes": 0,
            "runtime_entries": 4,
            "final_runtime_entries": 0,
            "removed_at_stage": "endpoint",
            "by_stage": {"endpoint": 4},
            "by_failure_category": {"connect_timeout": 2},
        }
    ]


def test_pipeline_consumes_self_hosted_carrier_payload(tmp_path: Path, monkeypatch) -> None:
    candidate, policies, mihomo = _pipeline_inputs(tmp_path)
    _success_services(monkeypatch)
    carrier_input = tmp_path / "carrier-qualification.json"
    carrier_input.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "collected_at_epoch": int(time.time()),
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
    assert carrier["freshness"]["status"] == "current"
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


def test_stage_attribution_covers_late_stage_removals(tmp_path: Path, monkeypatch) -> None:
    """A source removed by the browsing stage (after preflight passed) must be
    attributed to that stage with a non-empty aggregate failure reason."""

    _, policies, mihomo = _pipeline_inputs(tmp_path)
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text(
        "proxy-providers:\n"
        "  cr_browsing_jp:\n"
        "    payload:\n"
        "      - {name: '[BROWSING:JP] sub_4/One', type: trojan, server: a.example, port: 443}\n"
        "      - {name: '[BROWSING:JP] sub_4/Ip', type: ss, server: 8.8.4.4, port: 443}\n"
        "proxies: []\n"
        "proxy-groups: []\n",
        encoding="utf-8",
    )
    _preflight_stubs(monkeypatch)  # preflight passes everything
    _success_services_tail(monkeypatch)

    def browsing(**kwargs):
        document = load_yaml_file(kwargs["candidate"])
        for provider in document["proxy-providers"].values():
            provider["payload"] = [
                proxy for proxy in provider["payload"] if "sub_4/" not in str(proxy.get("name", ""))
            ]
        atomic_write(kwargs["candidate"], dump_yaml(document))
        _append(kwargs["candidate"], "browsing_stage")
        return {
            "status": "qualified",
            "automatic_nodes": 1,
            "diagnostics": {"failed_nodes": 1},
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

    removed_by_stage = result["removed_by_stage"]
    assert set(removed_by_stage) == {
        "hostname",
        "endpoint",
        "browsing",
        "transport",
        "ai",
        "service_hardening",
        "final",
    }
    assert removed_by_stage["hostname"]["runtime_entries"]["removed"] == 0
    browsing_stage = removed_by_stage["browsing"]
    assert browsing_stage["runtime_entries"]["removed"] == 2
    assert browsing_stage["by_source"] == {"sub_4": 2}
    assert browsing_stage["failure_category"] == {"browsing_qualification_failed": 2}
    assert removed_by_stage["transport"]["runtime_entries"]["removed"] == 0

    assert result["sources_fully_removed"] == [
        {
            "source": "sub_4",
            "unique_nodes": 2,
            "final_unique_nodes": 0,
            "runtime_entries": 2,
            "final_runtime_entries": 0,
            "removed_at_stage": "browsing",
            "by_stage": {"browsing": 2},
            "by_failure_category": {"browsing_qualification_failed": 2},
        }
    ]
    assert result["qualification_removed_runtime_entries"] == 2
    assert result["qualification_removed_unique_nodes"] == 2


def test_stage_accounting_never_leaks_entry_identities(tmp_path: Path, monkeypatch) -> None:
    candidate, policies, mihomo = _pipeline_inputs(tmp_path)
    candidate.write_text(
        "proxy-providers:\n"
        "  cr_browsing_jp:\n"
        "    payload:\n"
        "      - {name: '[BROWSING:JP] sub_2/Secret', type: trojan, server: secret-server.example, port: 443}\n"
        "proxies: []\n"
        "proxy-groups: []\n",
        encoding="utf-8",
    )
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

    serialized = json.dumps(
        {
            "removed_by_stage": result["removed_by_stage"],
            "sources_fully_removed": result["sources_fully_removed"],
            "removed_nodes": result["removed_nodes"],
        }
    )
    assert "secret-server.example" not in serialized
    assert "secret" not in serialized.casefold()
    assert "https://" not in serialized


def test_duplicate_runtime_entries_only_remove_unique_after_last_copy() -> None:
    first = {
        "source": "sub_4",
        "region": "jp",
        "protocol": "trojan",
        "unique": "sub_4|server|443|trojan",
    }
    before = {"browsing-copy": first, "general-copy": first}
    partial = {"general-copy": first}
    browsing = pipeline._stage_delta(
        before, partial, stage="browsing", reason="browsing_qualification_failed"
    )
    ai = pipeline._stage_delta(partial, {}, stage="ai", reason="ai_qualification_failed")

    assert browsing["unique_nodes"] == {"before": 1, "after": 1, "removed": 0}
    assert browsing["runtime_entries"] == {"before": 2, "after": 1, "removed": 1}
    assert ai["unique_nodes"] == {"before": 1, "after": 0, "removed": 1}
    assert ai["runtime_entries"]["removed"] == 1
    assert len({row["unique"] for row in before.values()} - set()) == 1
    for delta in (browsing, ai):
        unique = delta["unique_nodes"]
        assert unique["before"] == unique["after"] + unique["removed"]


@pytest.mark.parametrize("keep_general", [True, False])
def test_pipeline_duplicate_copies_across_stages_keep_unique_accounting_exact(
    tmp_path: Path, monkeypatch, keep_general: bool
) -> None:
    candidate, policies, mihomo = _pipeline_inputs(tmp_path)
    candidate.write_text(
        "proxy-providers:\n"
        "  cr_browsing_jp:\n"
        "    payload:\n"
        "      - {name: '[BROWSING:JP] sub_4/Solo', type: trojan, server: a.example, port: 443}\n"
        "  cr_general_jp:\n"
        "    payload:\n"
        "      - {name: '[GENERAL:ANY] sub_4/Solo', type: trojan, server: a.example, port: 443}\n"
        "proxies: []\nproxy-groups: []\n",
        encoding="utf-8",
    )
    _preflight_stubs(monkeypatch)
    _success_services_tail(monkeypatch)

    def browsing(**kwargs):
        document = load_yaml_file(kwargs["candidate"])
        document["proxy-providers"]["cr_browsing_jp"]["payload"] = []
        atomic_write(kwargs["candidate"], dump_yaml(document))
        return {"status": "qualified", "automatic_nodes": 1}

    def ai(**kwargs):
        if not keep_general:
            document = load_yaml_file(kwargs["candidate"])
            document["proxy-providers"]["cr_general_jp"]["payload"] = []
            atomic_write(kwargs["candidate"], dump_yaml(document))
        return _ai_summary()

    monkeypatch.setattr(pipeline, "run_browsing_qualification", browsing)
    monkeypatch.setattr(pipeline, "run_ai_qualification", ai)
    result = pipeline.run_qualification_pipeline(
        candidate=candidate,
        output=tmp_path / "final.yaml",
        policies=policies,
        mihomo_bin=mihomo,
        stage_dir=tmp_path / "stages",
        browsing_report=tmp_path / "browsing.json",
        ai_report=tmp_path / "ai.json",
    )
    browsing_delta = result["removed_by_stage"]["browsing"]
    ai_delta = result["removed_by_stage"]["ai"]
    assert browsing_delta["unique_nodes"] == {"before": 1, "after": 1, "removed": 0}
    assert browsing_delta["runtime_entries"]["removed"] == 1
    assert ai_delta["unique_nodes"]["removed"] == (0 if keep_general else 1)
    assert result["qualification_removed_unique_nodes"] == (0 if keep_general else 1)
    assert result["qualification_removed_runtime_entries"] == (1 if keep_general else 2)
    if keep_general:
        assert result["sources_fully_removed"] == []
    else:
        source = result["sources_fully_removed"][0]
        assert source["removed_at_stage"] == "ai"
        assert source["by_stage"] == {"ai": 1, "browsing": 1}
        assert source["runtime_entries"] == 2


def test_final_stage_runtime_drift_fails_closed() -> None:
    row = {
        "source": "sub_4",
        "region": "jp",
        "protocol": "trojan",
        "unique": "sub_4|server|443|trojan",
    }
    inventory = {"[BROWSING:JP] sub_4/One": row}
    document = {
        "proxy-providers": {
            "cr_browsing_jp": {
                "payload": [
                    {
                        "name": "[BROWSING:JP] sub_4/One",
                        "type": "trojan",
                        "server": "server",
                        "port": 443,
                    }
                ]
            }
        }
    }
    with pytest.raises(
        ValidationError, match="final qualification stage removed runtime inventory"
    ):
        pipeline._build_stage_accounting(
            preflight_inventory=inventory,
            post_host_inventory=inventory,
            post_endpoint_inventory=inventory,
            browsing_document=document,
            browsing_summary={},
            ai_document=document,
            service_document=document,
            final_document={"proxy-providers": {}},
            host_report={},
            endpoint_report={},
        )


def test_pipeline_rejects_unattributed_source_removal(tmp_path: Path, monkeypatch) -> None:
    candidate, policies, mihomo = _pipeline_inputs(tmp_path)
    _success_services(monkeypatch)
    monkeypatch.setattr(
        pipeline,
        "_sources_fully_removed",
        lambda **_kwargs: [
            {"removed_at_stage": "unattributed", "by_failure_category": {"unattributed": 1}}
        ],
    )
    with pytest.raises(ValidationError, match="lacks stage provenance"):
        pipeline.run_qualification_pipeline(
            candidate=candidate,
            output=tmp_path / "final.yaml",
            policies=policies,
            mihomo_bin=mihomo,
            stage_dir=tmp_path / "stages",
            browsing_report=tmp_path / "browsing.json",
            ai_report=tmp_path / "ai.json",
        )
