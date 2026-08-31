from __future__ import annotations

import socket

import pytest

from clash_relay.errors import FetchError
from clash_relay.fetch import _validate_resolved_destination


def _answer(address: str):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return (family, socket.SOCK_STREAM, 6, "", (address, 443))


def test_public_dns_answers_are_allowed(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            _answer("93.184.216.34"),
            _answer("2606:2800:220:1:248:1893:25c8:1946"),
        ],
    )

    _validate_resolved_destination("https://subscription.example/path")


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.2",
        "169.254.169.254",
        "192.168.1.1",
        "::1",
        "fc00::1",
        "fe80::1",
    ],
)
def test_private_or_special_dns_answer_is_rejected(monkeypatch, address: str) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [_answer(address)])

    with pytest.raises(FetchError, match="private or special-use"):
        _validate_resolved_destination("https://subscription.example/path")


def test_mixed_public_and_private_dns_answers_fail_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [_answer("93.184.216.34"), _answer("127.0.0.1")],
    )

    with pytest.raises(FetchError, match="private or special-use"):
        _validate_resolved_destination("https://subscription.example/path")


def test_localhost_name_is_rejected_before_resolution(monkeypatch) -> None:
    called = False

    def resolver(*args, **kwargs):
        nonlocal called
        called = True
        return [_answer("127.0.0.1")]

    monkeypatch.setattr(socket, "getaddrinfo", resolver)

    with pytest.raises(FetchError, match="localhost"):
        _validate_resolved_destination("https://anything.localhost/path")
    assert called is False


def test_dns_resolution_failure_is_redacted(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise socket.gaierror("resolver failed")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    url = "https://subscription.example/private/token?credential=secret"

    with pytest.raises(FetchError) as captured:
        _validate_resolved_destination(url)

    message = str(captured.value)
    assert "credential=secret" not in message
    assert "/private/token" not in message
    assert "subscription.example" in message
