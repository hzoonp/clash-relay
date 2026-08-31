from __future__ import annotations

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
