from __future__ import annotations

from clash_relay.browsing_qualification import _runtime_source_id, _vless_shape


def test_runtime_source_id_recovers_canonical_subscription_id() -> None:
    proxy = {
        "name": "[BROWSING:US] sub_3/Example #abcdef1234",
        "type": "vless",
    }

    assert _runtime_source_id(proxy) == "subscription_3"


def test_runtime_source_id_rejects_noncanonical_runtime_name() -> None:
    proxy = {"name": "private-node.example", "type": "vless"}

    assert _runtime_source_id(proxy) is None


def test_vless_shape_exposes_only_static_structural_categories() -> None:
    proxy = {
        "name": "[BROWSING:US] sub_5/Private #abcdef1234",
        "type": "vless",
        "network": "ws",
        "tls": True,
        "reality-opts": {
            "public-key": "private-public-key",
            "short-id": "private-short-id",
        },
        "flow": "private-flow-token",
        "packet-encoding": "xudp",
        "encryption": "none",
        "alpn": ["h2", "http/1.1"],
        "skip-cert-verify": False,
        "udp": True,
        "client-fingerprint": "chrome",
        "servername": "private.example",
        "ws-opts": {"path": "/private"},
    }

    assert _vless_shape(proxy) == {
        "network": "ws",
        "tls": "enabled",
        "reality": "mapping",
        "flow": "other",
        "packet_encoding": "xudp",
        "encryption": "none",
        "alpn": "list",
        "skip_cert_verify": "disabled",
        "udp": "enabled",
        "client_fingerprint": "chrome",
        "servername": "present",
        "transport_opts": "present",
        "reality_public_key": "present",
        "reality_short_id": "present",
    }
    assert "private" not in repr(_vless_shape(proxy))
