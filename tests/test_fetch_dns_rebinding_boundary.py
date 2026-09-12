from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from clash_relay.errors import FetchError
from clash_relay.fetch import fetch_subscription


@pytest.mark.xfail(
    strict=True,
    reason="known DNS validation/connection TOCTOU: preflight DNS is not pinned to the socket",
)
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

    for name in (
        "http_proxy",
        "HTTP_PROXY",
        "https_proxy",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
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

        # Desired invariant: rejecting the rebound destination must happen before
        # any request can reach the private listener. Current code violates this
        # because urllib performs a second DNS lookup when opening the socket.
        assert hits == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
