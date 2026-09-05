from __future__ import annotations

import socket
import urllib.error

import pytest

import clash_relay.transport_qualification as transport
from clash_relay.errors import ValidationError


class _RecvSocket:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)

    def recv(self, length: int) -> bytes:
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if len(chunk) <= length:
            return chunk
        self.chunks.insert(0, chunk[length:])
        return chunk[:length]


def _provider_config() -> dict:
    return {
        "proxy-providers": {
            "cr_general_a": {
                "type": "inline",
                "health-check": {"enable": True},
                "payload": [
                    {
                        "name": "node.a",
                        "type": "ss",
                        "server": "example.com",
                        "port": 443,
                    }
                ],
            },
            "cr_ai_ignored": {
                "type": "inline",
                "payload": [
                    {
                        "name": "ai",
                        "type": "ss",
                        "server": "ai.example",
                        "port": 443,
                    }
                ],
            },
        },
        "dns": {"enable": True, "listen": "0.0.0.0:53"},
    }


def test_general_provider_payloads_are_normalized_and_fail_closed() -> None:
    result = transport._general_provider_payloads(_provider_config())
    assert list(result) == ["cr_general_a"]
    assert result["cr_general_a"][0]["name"] == "node.a"

    with pytest.raises(ValidationError, match="requires proxy-providers"):
        transport._general_provider_payloads({})
    with pytest.raises(ValidationError, match="no general provider inventory"):
        transport._general_provider_payloads({"proxy-providers": {}})
    with pytest.raises(ValidationError, match="payload is invalid"):
        transport._general_provider_payloads(
            {"proxy-providers": {"cr_general_bad": {"type": "inline", "payload": []}}}
        )
    with pytest.raises(ValidationError, match="unnamed proxy"):
        transport._general_provider_payloads(
            {
                "proxy-providers": {
                    "cr_general_bad": {"type": "inline", "payload": [{"type": "ss"}]}
                }
            }
        )


def test_temporary_probe_config_is_private_inline_and_rebinds_dns(monkeypatch) -> None:
    base = _provider_config()
    payloads = transport._general_provider_payloads(base)
    monkeypatch.setattr(transport, "_free_port", lambda: 5353)

    probe = transport._temporary_probe_config(
        base,
        payloads,
        mixed_port=7891,
        controller_port=9091,
        secret="secret",
    )

    provider = probe["proxy-providers"]["cr_general_a"]
    assert provider["type"] == "inline"
    assert "health-check" not in provider
    assert provider["payload"][0]["name"] == "node.a"
    assert probe["external-controller"] == "127.0.0.1:9091"
    assert probe["dns"]["listen"] == "127.0.0.1:5353"
    assert probe["rules"] == ["MATCH,__CR_TRANSPORT_QUALIFICATION"]


def test_delay_probe_filters_invalid_values_and_degrades_on_network_failure(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        transport,
        "_controller_get",
        lambda *args, **kwargs: {"good": 42, "negative": -1, "bool": True, 3: 9},
    )
    assert transport._group_delay_probe(9090, "secret") == {"good": 42, "bool": True}

    def fail(*args, **kwargs):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(transport, "_controller_get", fail)
    assert transport._group_delay_probe(9090, "secret") == {}


def test_recv_exact_and_socks_address_decoding() -> None:
    sock = _RecvSocket([b"ab", b"cd"])
    assert transport._recv_exact(sock, 4) == b"abcd"

    with pytest.raises(OSError, match="unexpected SOCKS5 EOF"):
        transport._recv_exact(_RecvSocket([]), 1)

    ipv4 = _RecvSocket([socket.inet_pton(socket.AF_INET, "127.0.0.1")])
    assert transport._read_socks_address(ipv4, 1) == "127.0.0.1"

    domain = _RecvSocket([b"\x0b", b"example.com"])
    assert transport._read_socks_address(domain, 3) == "example.com"

    with pytest.raises(OSError, match="unsupported SOCKS5 address type"):
        transport._read_socks_address(_RecvSocket([]), 99)


def test_udp_transport_probe_prefers_quic_then_falls_back_to_dns(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    def quic_success(mixed_port: int, target: str, port: int, payload: bytes) -> bool:
        calls.append((target, port))
        return True

    monkeypatch.setattr(transport, "_socks_udp_roundtrip", quic_success)
    assert transport._udp_transport_probe(7890) == (True, True)
    assert calls == [("1.1.1.1", 443)]

    calls.clear()

    def dns_fallback(mixed_port: int, target: str, port: int, payload: bytes) -> bool:
        calls.append((target, port))
        return port == 53

    monkeypatch.setattr(transport, "_socks_udp_roundtrip", dns_fallback)
    assert transport._udp_transport_probe(7890) == (True, False)
    assert calls == [("1.1.1.1", 443), ("1.1.1.1", 53)]


def test_group_rewrite_rejects_invalid_structure_and_custom_filter() -> None:
    with pytest.raises(ValidationError, match="proxy group structure"):
        transport._rewrite_group({}, "自动选择", {"node"})

    config = {"proxy-groups": [{"name": "自动选择", "type": "select"}]}
    with pytest.raises(ValidationError, match="requires url-test group"):
        transport._rewrite_group(config, "自动选择", {"node"})

    config = {
        "proxy-groups": [{"name": "自动选择", "type": "url-test", "filter": "custom"}]
    }
    with pytest.raises(ValidationError, match="cannot compose custom filter"):
        transport._rewrite_group(config, "自动选择", {"node"})


def test_apply_transport_qualification_rejects_empty_tcp_inventory() -> None:
    with pytest.raises(ValidationError, match="no TCP-qualified nodes"):
        transport.apply_transport_qualification({"proxy-groups": []}, set(), set())
