from __future__ import annotations

import socket
import threading
import time

import pytest

from clash_relay import fetch
from clash_relay.errors import FetchError


@pytest.mark.parametrize(
    "prefix,drip",
    [
        (b"HTTP/1.1 200 OK\r\nX-Slow: ", b"a" * 40),
        (
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n",
            b" " * 40,
        ),
        (
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n28\r\n",
            b"a" * 40,
        ),
    ],
    ids=["response-headers", "chunk-size", "chunk-body"],
)
def test_network_deadline_interrupts_dripping_http_input(monkeypatch, prefix, drip) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        finished = threading.Event()

        def serve() -> None:
            try:
                with listener.accept()[0] as client:
                    client.settimeout(1)
                    client.recv(4096)
                    client.sendall(prefix)
                    for char in drip:
                        time.sleep(0.03)
                        client.sendall(bytes((char,)))
            except OSError:
                pass
            finally:
                finished.set()

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        monkeypatch.setattr(
            fetch,
            "_resolve_public_destination",
            lambda *_args, **_kwargs: (
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port)),
            ),
        )
        start = time.monotonic()
        with pytest.raises(FetchError, match="total timeout"):
            fetch.fetch_subscription(
                f"http://public.invalid:{port}/subscription",
                timeout=0.15,
                max_bytes=1024,
                allow_http=True,
                allow_file=False,
            )
        assert time.monotonic() - start < 0.6
        assert finished.wait(0.6)
