from __future__ import annotations

import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from clash_relay import fetch
from clash_relay.errors import FetchError
from clash_relay.fetch import fetch_subscription


def test_dns_rebinding_must_not_reach_private_address_on_second_resolution(
    monkeypatch,
) -> None:
    hits = 0

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            nonlocal hits
            hits += 1
            payload = b"proxies: []\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/yaml")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    hostname = "rebinding.invalid"
    port = server.server_port
    real_getaddrinfo = socket.getaddrinfo
    resolution_count = 0

    def rebound_getaddrinfo(host: str, requested_port: int, *args, **kwargs):
        nonlocal resolution_count
        if host != hostname:
            return real_getaddrinfo(host, requested_port, *args, **kwargs)
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

    try:
        with pytest.raises(FetchError, match="private or special-use"):
            fetch_subscription(
                f"http://{hostname}:{port}/subscription.yaml",
                timeout=2,
                max_bytes=64 * 1024,
                allow_http=True,
                allow_file=False,
            )

        assert resolution_count >= 2
        assert hits == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


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

    monkeypatch.setattr(
        "clash_relay.fetch._validate_resolved_destination",
        lambda url, **kwargs: None,
    )
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
    monkeypatch.setattr(
        fetch,
        "_validate_resolved_destination",
        lambda url, *, deadline: deadline.remaining(),
    )

    with pytest.raises(FetchError, match="total timeout"):
        handler.redirect_request(None, None, 302, "Found", None, "http://public.invalid/path")
