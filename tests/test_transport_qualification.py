from __future__ import annotations

import pytest

import clash_relay.transport_qualification as transport_qualification
from clash_relay.errors import ValidationError
from clash_relay.transport_qualification import (
    _quic_probe_payload,
    apply_transport_qualification,
)


def _config() -> dict:
    return {
        "proxy-groups": [
            {"name": "自动选择", "type": "url-test", "filter": ".*"},
            {"name": "媒体自动", "type": "url-test", "filter": ".*"},
            {"name": "通讯自动", "type": "url-test", "filter": ".*"},
        ]
    }


def _group(config: dict, name: str) -> dict:
    return next(item for item in config["proxy-groups"] if item["name"] == name)


def test_transport_qualification_keeps_tcp_general_and_udp_sensitive_groups_separate() -> None:
    config = _config()

    report = apply_transport_qualification(
        config,
        {"node-a", "node.b"},
        {"node.b"},
    )

    assert report["general_automatic_nodes"] == 2
    assert report["udp_automatic_nodes"] == 1
    assert "node-a" in _group(config, "自动选择")["filter"]
    assert "node\\.b" in _group(config, "自动选择")["filter"]
    assert "node-a" not in _group(config, "媒体自动")["filter"]
    assert "node\\.b" in _group(config, "媒体自动")["filter"]
    assert _group(config, "通讯自动")["filter"] == _group(config, "媒体自动")["filter"]


def test_transport_qualification_fails_closed_without_udp_nodes() -> None:
    with pytest.raises(ValidationError, match="no UDP-qualified nodes"):
        apply_transport_qualification(_config(), {"node-a"}, set())


def test_quic_probe_is_amplification_safe_unsupported_version_datagram() -> None:
    payload = _quic_probe_payload()

    assert len(payload) == 1200
    assert payload[0] & 0xC0 == 0xC0
    assert int.from_bytes(payload[1:5], "big") == 0x0A0A0A0A
    assert payload[5] == 8
    assert payload[6:14] == b"p13dcid1"
    assert payload[14] == 8
    assert payload[15:23] == b"p13scid1"


def test_transport_probe_detaches_production_dns_rule_set_policy() -> None:
    proxy = {"name": "node-a", "type": "http", "server": "a.invalid", "port": 443}
    base = {
        "proxy-providers": {
            "cr_general_any": {"type": "inline", "payload": [proxy]}
        },
        "dns": {
            "enable": True,
            "listen": "127.0.0.1:1053",
            "nameserver-policy": {
                "rule-set:acl4ssr_china_domain": ["https://dns.alidns.com/dns-query"]
            },
        },
    }

    probe = transport_qualification._temporary_probe_config(
        base,
        {"cr_general_any": (proxy,)},
        mixed_port=17892,
        controller_port=19092,
        secret="test",
    )

    assert "nameserver-policy" not in probe["dns"]
