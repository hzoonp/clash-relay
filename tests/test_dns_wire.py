from __future__ import annotations

import base64
import io
import socket
import struct
import urllib.error

import pytest

from clash_relay.dns_wire import build_query, parse_response, probe_doh


def _name_wire(name: str, *, compress_to: int | None = None) -> bytes:
    if compress_to is not None:
        return bytes([0xC0, compress_to])
    wire = b""
    for label in name.rstrip(".").split("."):
        wire += bytes([len(label)]) + label.encode()
    return wire + b"\x00"


def _response(
    *,
    rcode: int = 0,
    answers: list[tuple[int, bytes]] | None = None,
    name: str = "example.example",
    compressed: bool = False,
    query: bool = False,
) -> bytes:
    question_name = _name_wire(name)
    question = question_name + struct.pack("!HH", 1, 1)
    flags = (0x8000 if not query else 0) | 0x0100 | rcode  # QR + RD + rcode
    header = struct.pack("!HHHHHH", 0x1234, flags, 1, len(answers or []), 0, 0)
    body = b""
    for rtype, rdata in answers or []:
        answer_name = _name_wire(name, compress_to=12) if compressed else _name_wire(name)
        body += answer_name + struct.pack("!HHIH", rtype, 1, 300, len(rdata)) + rdata
    return header + question + body


def test_build_query_encodes_a_standard_a_query() -> None:
    query = build_query("Proxy.Example.com")

    ident, flags, qdcount, ancount, nscount, arcount = struct.unpack("!HHHHHH", query[:12])
    assert ident == 0
    assert flags == 0x0100  # recursion desired, standard query
    assert (qdcount, ancount, nscount, arcount) == (1, 0, 0, 0)
    assert query.endswith(struct.pack("!HH", 1, 1))  # QTYPE=A, QCLASS=IN
    assert b"\x05Proxy\x07Example\x03com\x00" in query


@pytest.mark.parametrize("hostname", ["", ".", "example..com", "a" * 64 + ".com"])
def test_build_query_rejects_invalid_names(hostname: str) -> None:
    with pytest.raises(ValueError):
        build_query(hostname)


def test_parse_response_accepts_public_a_answer() -> None:
    assert parse_response(_response(answers=[(1, socket.inet_aton("93.184.216.34"))])) == (
        True,
        "answered",
    )


def test_parse_response_accepts_public_aaaa_answer() -> None:
    assert parse_response(
        _response(answers=[(28, socket.inet_pton(socket.AF_INET6, "2606:2800:220:1::1"))])
    ) == (True, "answered")


def test_parse_response_ignores_private_answers() -> None:
    assert parse_response(_response(answers=[(1, socket.inet_aton("10.0.0.1"))])) == (
        False,
        "no_answer",
    )


def test_parse_response_classifies_rcodes() -> None:
    assert parse_response(_response(rcode=3)) == (False, "nxdomain")
    assert parse_response(_response(rcode=2)) == (False, "server_failure")
    assert parse_response(_response()) == (False, "no_answer")


def test_parse_response_handles_compressed_answer_names() -> None:
    payload = _response(
        answers=[(1, socket.inet_aton("93.184.216.34"))],
        compressed=True,
    )
    assert parse_response(payload) == (True, "answered")


def test_parse_response_rejects_queries_and_garbage() -> None:
    assert parse_response(_response(query=True)) == (False, "malformed_response")
    assert parse_response(b"\x00") == (False, "malformed_response")
    assert parse_response(b"") == (False, "malformed_response")


def test_parse_response_ignores_cname_chain_without_address() -> None:
    cname = _name_wire("target.example")
    payload = _response(answers=[(5, cname)])
    assert parse_response(payload) == (False, "no_answer")


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._stream = io.BytesIO(payload)

    def read(self, _amount: int) -> bytes:
        return self._stream.read()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_probe_doh_sends_rfc8484_wireformat(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["accept"] = request.headers.get("Accept")
        captured["timeout"] = timeout
        return _FakeResponse(_response(answers=[(1, socket.inet_aton("93.184.216.34"))]))

    monkeypatch.setattr("clash_relay.dns_wire.urllib.request.urlopen", fake_urlopen)

    assert probe_doh("https://dns.alidns.com/dns-query", "node.example") == (True, "answered")

    url = str(captured["url"])
    assert url.startswith("https://dns.alidns.com/dns-query?dns=")
    encoded = url.split("?dns=", 1)[1]
    query = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    assert query == build_query("node.example")
    assert captured["accept"] == "application/dns-message"


def test_probe_doh_classifies_transport_failures(monkeypatch) -> None:
    def raise_http_error(*_args, **_kwargs):
        raise urllib.error.HTTPError("https://resolver/dns-query", 503, "oops", {}, io.BytesIO())

    monkeypatch.setattr("clash_relay.dns_wire.urllib.request.urlopen", raise_http_error)
    assert probe_doh("https://resolver/dns-query", "node.example") == (False, "http_error")

    def raise_refused(*_args, **_kwargs):
        raise urllib.error.URLError(ConnectionRefusedError())

    monkeypatch.setattr("clash_relay.dns_wire.urllib.request.urlopen", raise_refused)
    assert probe_doh("https://resolver/dns-query", "node.example") == (False, "refused")

    def raise_timeout(*_args, **_kwargs):
        raise urllib.error.URLError(TimeoutError())

    monkeypatch.setattr("clash_relay.dns_wire.urllib.request.urlopen", raise_timeout)
    assert probe_doh("https://resolver/dns-query", "node.example") == (False, "connect_timeout")

    def raise_gaierror(*_args, **_kwargs):
        raise urllib.error.URLError(socket.gaierror())

    monkeypatch.setattr("clash_relay.dns_wire.urllib.request.urlopen", raise_gaierror)
    assert probe_doh("https://resolver/dns-query", "node.example") == (False, "dns_failure")

    def raise_oserror(*_args, **_kwargs):
        raise OSError("boom")

    monkeypatch.setattr("clash_relay.dns_wire.urllib.request.urlopen", raise_oserror)
    assert probe_doh("https://resolver/dns-query", "node.example") == (False, "transport_error")


def test_probe_doh_classifies_wireformat_payloads(monkeypatch) -> None:
    cases = {
        "nxdomain": _response(rcode=3),
        "no_answer": _response(),
        "server_failure": _response(rcode=2),
    }
    for expected, payload in cases.items():
        monkeypatch.setattr(
            "clash_relay.dns_wire.urllib.request.urlopen",
            lambda _request, timeout=None, _payload=payload: _FakeResponse(_payload),
        )
        assert probe_doh("https://resolver/dns-query", "node.example") == (False, expected)
