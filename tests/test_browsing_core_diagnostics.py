from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import clash_relay.core_diagnostics as diagnostics
from clash_relay.core_diagnostics import _safe_structural_detail, redact_core_output


def _candidate() -> dict:
    return {
        "proxy-providers": {
            "cr_browsing_any": {
                "payload": [
                    {
                        "name": "Sensitive Node Name",
                        "server": "203.0.113.77",
                        "password": "super-secret-password",
                        "uuid": "12345678-1234-1234-1234-123456789abc",
                        "grpc-opts": {
                            "grpc-service-name": "secret-service-name",
                            "bad": ["secret-value"],
                        },
                    }
                ]
            }
        }
    }


def test_browsing_core_diagnostic_redacts_proxy_values() -> None:
    candidate = _candidate()
    raw = (
        "proxy Sensitive Node Name failed at 203.0.113.77 with "
        "super-secret-password and 12345678-1234-1234-1234-123456789abc"
    )

    cleaned = redact_core_output(raw, candidate)

    assert "Sensitive Node Name" not in cleaned
    assert "203.0.113.77" not in cleaned
    assert "super-secret-password" not in cleaned
    assert "12345678-1234-1234-1234-123456789abc" not in cleaned
    assert "<redacted>" in cleaned


def test_browsing_core_diagnostic_is_bounded_and_normalized() -> None:
    raw = "198.51.100.42\n" + ("a" * 40) + "\r\n" + ("detail " * 300)

    cleaned = redact_core_output(raw, {"proxy-providers": {}})

    assert "198.51.100.42" not in cleaned
    assert "a" * 40 not in cleaned
    assert "<address>" in cleaned
    assert "<token>" in cleaned
    assert "\n" not in cleaned
    assert "\r" not in cleaned
    assert len(cleaned) <= diagnostics._MAX_DIAGNOSTIC_CHARS


def test_safe_structural_detail_reports_field_and_types_without_values() -> None:
    raw = "parse proxy provider cr_browsing_any error: filed payload[0][grpc-opts] invalid"

    detail = _safe_structural_detail(raw, _candidate())

    assert detail == {
        "provider": "cr_browsing_any",
        "payload_index": 0,
        "invalid_field": "grpc-opts",
        "field_shape": {
            "type": "mapping",
            "items": 2,
            "value_types": {"list": 1, "str": 1},
        },
    }
    rendered = str(detail)
    assert "Sensitive Node Name" not in rendered
    assert "secret-service-name" not in rendered
    assert "secret-value" not in rendered


def test_safe_structural_detail_handles_unknown_and_out_of_range_payloads() -> None:
    candidate = _candidate()

    assert _safe_structural_detail("unstructured core error", candidate) == {}
    assert _safe_structural_detail(
        "parse proxy provider cr_browsing_any error: field payload[9][server] invalid",
        candidate,
    ) == {
        "provider": "cr_browsing_any",
        "payload_index": 9,
        "invalid_field": "server",
    }


def _stub_probe_build(monkeypatch: pytest.MonkeyPatch, candidate: dict) -> None:
    monkeypatch.setattr(diagnostics, "load_yaml_file", lambda path: candidate)
    monkeypatch.setattr(
        diagnostics,
        "_browsing_provider_payloads",
        lambda value: value["proxy-providers"],
    )
    monkeypatch.setattr(diagnostics, "_free_port", lambda: 12345)
    monkeypatch.setattr(
        diagnostics,
        "_temporary_probe_config",
        lambda *args, **kwargs: {"mixed-port": 12345, "proxies": []},
    )


def test_diagnose_browsing_core_rejects_non_mapping_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(diagnostics, "load_yaml_file", lambda path: [])

    result = diagnostics.diagnose_browsing_core(tmp_path / "candidate.yaml", tmp_path / "mihomo")

    assert result == {"status": "unavailable", "reason": "candidate_not_mapping"}


def test_diagnose_browsing_core_reports_valid_retry_without_raw_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate()
    _stub_probe_build(monkeypatch, candidate)
    monkeypatch.setattr(
        diagnostics.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="Sensitive Node Name"),
    )

    result = diagnostics.diagnose_browsing_core(tmp_path / "candidate.yaml", tmp_path / "mihomo")

    assert result == {"status": "valid_on_retry", "returncode": 0}


def test_diagnose_browsing_core_rejection_remains_structural_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate()
    _stub_probe_build(monkeypatch, candidate)
    raw = (
        "parse proxy provider cr_browsing_any error: field payload[0][grpc-opts] invalid; "
        "Sensitive Node Name 203.0.113.77 super-secret-password"
    )
    monkeypatch.setattr(
        diagnostics.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=raw),
    )

    result = diagnostics.diagnose_browsing_core(tmp_path / "candidate.yaml", tmp_path / "mihomo")
    rendered = str(result)

    assert result["status"] == "rejected"
    assert result["returncode"] == 1
    assert result["provider"] == "cr_browsing_any"
    assert result["payload_index"] == 0
    assert result["invalid_field"] == "grpc-opts"
    assert result["field_shape"] == {
        "type": "mapping",
        "items": 2,
        "value_types": {"list": 1, "str": 1},
    }
    assert "Sensitive Node Name" not in rendered
    assert "203.0.113.77" not in rendered
    assert "super-secret-password" not in rendered
