from __future__ import annotations

from scripts.diagnose_browsing_core import redact_core_output


def test_browsing_core_diagnostic_redacts_proxy_values() -> None:
    candidate = {
        "proxy-providers": {
            "cr_browsing_any": {
                "payload": [
                    {
                        "name": "Sensitive Node Name",
                        "server": "203.0.113.77",
                        "password": "super-secret-password",
                        "uuid": "12345678-1234-1234-1234-123456789abc",
                    }
                ]
            }
        }
    }
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
