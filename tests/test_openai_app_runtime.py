from __future__ import annotations

import ssl
import urllib.error

from clash_relay.ai_qualification import _network_outcome, _temporary_probe_config


def _sniffer() -> dict:
    return {
        "enable": True,
        "force-dns-mapping": False,
        "parse-pure-ip": True,
        "sniff": {
            "HTTP": {"ports": [80, "8080-8880"], "override-destination": True},
            "TLS": {"ports": [443, 8443]},
            "QUIC": {"ports": [443, 8443]},
        },
    }


def test_ai_probe_runtime_preserves_production_sniffer_without_owning_dns() -> None:
    base = {
        "ipv6": False,
        "sniffer": _sniffer(),
        "proxy-providers": {
            "cr_ai_us_us": {
                "type": "inline",
                "payload": [{"name": "node", "type": "http", "server": "example.com", "port": 443}],
            }
        },
    }

    temporary = _temporary_probe_config(
        base,
        provider_name="cr_ai_us_us",
        payload=tuple(base["proxy-providers"]["cr_ai_us_us"]["payload"]),
        mixed_port=17890,
        controller_port=19090,
        secret="test",
    )

    assert temporary["sniffer"] == _sniffer()
    assert "dns" not in temporary


def test_certificate_validation_failure_is_a_hard_tls_error() -> None:
    error = urllib.error.URLError(ssl.SSLCertVerificationError("hostname mismatch"))

    assert _network_outcome(error) == "tls_error"
