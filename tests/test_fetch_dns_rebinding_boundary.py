from __future__ import annotations

import socket
import threading
import urllib.request

import pytest

from clash_relay import fetch
from clash_relay.errors import FetchError
from clash_relay.fetch import fetch_subscription


def test_fetch_connects_only_to_its_single_validated_dns_answer(monkeypatch) -> None:
    hostname = "rebinding.invalid"
    resolution_count = 0
    connected_answers = []

    def rebound_getaddrinfo(host: str, requested_port: int, *args, **kwargs):
        nonlocal resolution_count
        assert host == hostname
        resolution_count += 1
        address = "93.184.216.34" if resolution_count == 1 else "127.0.0.1"
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (address, requested_port),
            )
        ]

    monkeypatch.setattr(socket, "getaddrinfo", rebound_getaddrinfo)

    def capture_connect(answers, **kwargs):
        connected_answers.extend(answers)
        raise OSError("connection stopped by test")

    monkeypatch.setattr(fetch, "_connect_resolved", capture_connect)
    with pytest.raises(FetchError, match="subscription fetch failed"):
        fetch_subscription(
            f"http://{hostname}:8080/subscription.yaml",
            timeout=2,
            max_bytes=64 * 1024,
            allow_http=True,
            allow_file=False,
        )

    assert resolution_count == 1
    assert [answer[4][0] for answer in connected_answers] == ["93.184.216.34"]


def test_each_redirect_target_is_checked_before_connect(monkeypatch) -> None:
    def private_resolver(host: str, port: int, *args, **kwargs):
        assert host == "redirect.invalid"
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port))]

    monkeypatch.setattr(socket, "getaddrinfo", private_resolver)
    handler = fetch._PinnedHTTPHandler(deadline=fetch._Deadline(2))
    monkeypatch.setattr(
        handler,
        "do_open",
        lambda *args, **kwargs: pytest.fail("private redirect reached the connection step"),
    )

    with pytest.raises(FetchError, match="private or special-use"):
        handler.http_open(urllib.request.Request("http://redirect.invalid/subscription.yaml"))


def test_subscription_fetch_opener_explicitly_disables_environment_proxies(
    monkeypatch,
) -> None:
    captured = []
    requests = []

    class FakeResponse:
        def __init__(self) -> None:
            self.headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def geturl(self) -> str:
            return "http://public.invalid/subscription.yaml"

        def read(self, size: int = -1) -> bytes:
            return b""

    class FakeOpener:
        def open(self, request, timeout):
            requests.append(request)
            return FakeResponse()

    def fake_build_opener(*handlers):
        captured.extend(handlers)
        return FakeOpener()

    monkeypatch.setattr("clash_relay.fetch.urllib.request.build_opener", fake_build_opener)

    assert (
        fetch_subscription(
            "http://public.invalid/subscription.yaml",
            timeout=2,
            max_bytes=64 * 1024,
            allow_http=True,
            allow_file=False,
            client_profile="mihomo",
        )
        == ""
    )

    proxy_handlers = [
        handler for handler in captured if isinstance(handler, urllib.request.ProxyHandler)
    ]
    assert len(proxy_handlers) == 1
    assert proxy_handlers[0].proxies == {}
    assert len(requests) == 1
    assert requests[0].get_header("User-agent") == "clash.meta"


def test_dns_resolution_consumes_the_shared_subscription_deadline(monkeypatch) -> None:
    ticks = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(fetch.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(
        fetch.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))
        ],
    )

    with pytest.raises(FetchError, match="total timeout"):
        fetch._resolve_public_destination(
            "https://subscription.invalid.example/path",
            deadline=fetch._Deadline(1.0),
        )


def test_response_reads_stop_when_the_shared_deadline_expires(monkeypatch) -> None:
    ticks = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(fetch.time, "monotonic", lambda: next(ticks))

    class Response:
        def read(self, size: int) -> bytes:
            return b"chunk"

    with pytest.raises(FetchError, match="total timeout"):
        fetch._read_bounded(Response(), 1024, deadline=fetch._Deadline(1.0))


def test_network_response_prefers_single_system_read_when_available() -> None:
    class Response:
        def __init__(self) -> None:
            self.chunks = iter((b"first", b"second", b""))

        def read(self, size: int) -> bytes:
            pytest.fail("network response used a filling read")

        def read1(self, size: int) -> bytes:
            return next(self.chunks)

    assert fetch._read_bounded(Response(), 20, deadline=fetch._Deadline(1), read_once=True) == (
        b"firstsecond"
    )


def test_redirect_validation_observes_the_shared_deadline(monkeypatch) -> None:
    ticks = iter((0.0, 2.0))
    monkeypatch.setattr(fetch.time, "monotonic", lambda: next(ticks))
    deadline = fetch._Deadline(1.0)
    handler = fetch._SafeRedirectHandler(allow_http=True, allow_file=False, deadline=deadline)
    monkeypatch.setattr(
        fetch.urllib.request.HTTPRedirectHandler,
        "redirect_request",
        lambda *args, **kwargs: None,
    )
    with pytest.raises(FetchError, match="total timeout"):
        handler.redirect_request(None, None, 302, "Found", None, "http://public.invalid/path")


def test_stalled_dns_returns_at_deadline(monkeypatch) -> None:
    release = threading.Event()
    started = threading.Event()

    def stalled_resolver(*args, **kwargs):
        started.set()
        release.wait(timeout=2)
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))
        ]

    monkeypatch.setattr(socket, "getaddrinfo", stalled_resolver)
    try:
        with pytest.raises(FetchError, match="total timeout"):
            fetch._resolve_public_destination(
                "https://subscription.invalid.example/path",
                deadline=fetch._Deadline(0.05),
            )
        assert started.is_set()
    finally:
        release.set()
