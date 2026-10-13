"""Real Mihomo must route the VMess ``grpc`` service name onto the wire.

``mihomo -t`` only proves the generated YAML loads. This fixture starts the real
pinned core, points the parsed ``vmess`` node at a local upstream that speaks
just enough HTTP/2, and lets Mihomo dial it through the proxy. The captured
client stream proves a real gRPC/HTTP/2 connection is established, and the size
of the HEADERS block is a deterministic function of the configured
``grpc-opts.grpc-service-name``: holding everything else constant, a longer
service name produces a strictly longer HPACK block. That couples the byte
stream to the service name without relying on a decoding library.
"""

from __future__ import annotations

import base64
import contextlib
import itertools
import json
import os
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from _mihomo import wait_until

from clash_relay.subscription_parser import parse_subscription
from clash_relay.util import dump_yaml

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures" / "uri" / "vmess-grpc.yaml"
HTTP2_PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"

# A fixed upstream port keeps ``:authority`` identical between runs so the only
# wire-level variable left is the configured gRPC service name.
UPSTREAM_PORT = 28987
SERVICE_NAMES = ("svc", "fixture-service", "fixture-service-abcdefghij")


def _binary() -> Path:
    configured = os.environ.get("MIHOMO_BIN")
    if not configured:
        pytest.skip("MIHOMO_BIN is not set")
    return Path(configured).resolve()


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _frame(kind: int, flags: int, stream: int, payload: bytes = b"") -> bytes:
    return (
        len(payload).to_bytes(3, "big") + bytes([kind, flags]) + stream.to_bytes(4, "big") + payload
    )


def _vmess_uri(service_name: str | None) -> str:
    data = {
        "v": "2",
        "ps": f"grpc-{service_name}",
        "add": "node.invalid.example",
        "port": "443",
        "id": "00000000-0000-4000-8000-000000000003",
        "aid": "0",
        "scy": "auto",
        "net": "grpc",
        "path": service_name or "",
        "tls": "",
    }
    return "vmess://" + base64.b64encode(json.dumps(data).encode()).decode()


@contextlib.contextmanager
def _capturing_grpc_upstream(port: int) -> Iterator[list[bytes]]:
    """Accept one gRPC-adjacent HTTP/2 client stream and record its bytes."""

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(4)
    listener.settimeout(30)

    captured: list[bytes] = []
    ready = threading.Event()

    def serve() -> None:
        try:
            connection, _ = listener.accept()
        except (OSError, TimeoutError):
            return
        with connection:
            connection.settimeout(5)
            try:
                captured.append(connection.recv(65536))
                connection.sendall(_frame(0x4, 0, 0))
                connection.sendall(_frame(0x4, 0x1, 0))
                ready.set()
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        chunk = connection.recv(65536)
                    except (OSError, TimeoutError):
                        break
                    if not chunk:
                        break
                    captured.append(chunk)
            except (OSError, TimeoutError):
                pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield captured
    finally:
        ready.wait(timeout=10)
        listener.close()
        thread.join(timeout=6)


def _headers_payload(blob: bytes) -> bytes:
    """Return the payload of the first client HEADERS frame, if any."""

    index = len(HTTP2_PREFACE)
    while index + 9 <= len(blob):
        length = int.from_bytes(blob[index : index + 3], "big")
        kind = blob[index + 3]
        payload = blob[index + 9 : index + 9 + length]
        if len(payload) != length:
            raise AssertionError("captured stream is not a complete HTTP/2 frame sequence")
        if kind == 1:
            assert payload, "HEADERS frame carried no HPACK block"
            return payload
        index += 9 + length
    raise AssertionError("Mihomo never sent an HTTP/2 HEADERS frame")


def _observed_headers_payload(service_name: str, workdir: Path) -> bytes:
    proxy = parse_subscription(_vmess_uri(service_name), reject_private_hosts=False).proxies[0]
    proxy["server"] = "127.0.0.1"
    proxy["port"] = UPSTREAM_PORT

    controller_port = _port()
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "ui").mkdir(exist_ok=True)
    target = workdir / "config.yaml"
    target.write_text(
        dump_yaml(
            {
                "mixed-port": _port(),
                "allow-lan": False,
                "mode": "rule",
                "log-level": "info",
                "external-controller": f"127.0.0.1:{controller_port}",
                "proxies": [proxy],
                "proxy-groups": [{"name": "Fixture", "type": "select", "proxies": [proxy["name"]]}],
                "rules": ["MATCH,Fixture"],
            }
        ),
        encoding="utf-8",
    )

    with _capturing_grpc_upstream(UPSTREAM_PORT) as captured:
        log = (workdir / "mihomo.log").open("wb")
        process = subprocess.Popen(
            [_binary(), "-d", str(workdir), "-f", str(target)],
            cwd=workdir,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:

            def controller_ready() -> bool:
                if process.poll() is not None:
                    raise AssertionError(f"Mihomo exited early: {log.name}")
                encoded = urllib.parse.quote(str(proxy["name"]), safe="")
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{controller_port}/proxies/{encoded}", timeout=1
                ) as response:
                    json.load(response)
                return True

            wait_until(controller_ready, timeout=20, label="Mihomo controller")

            encoded = urllib.parse.quote(str(proxy["name"]), safe="")
            probe_url = urllib.parse.quote("http://127.0.0.1:1/healthz", safe="")
            with (
                contextlib.suppress(Exception),
                urllib.request.urlopen(
                    f"http://127.0.0.1:{controller_port}/proxies/{encoded}"
                    f"/delay?url={probe_url}&timeout=2000",
                    timeout=5,
                ) as response,
            ):
                response.read()
        finally:
            if process.poll() is None:
                process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=10)
            log.close()

    blob = b"".join(captured)
    (workdir / "client-stream.bin").write_bytes(blob)
    return _headers_payload(blob)


def test_vmess_grpc_service_name_scales_the_mihomo_client_stream(tmp_path: Path) -> None:
    lengths: list[int] = []
    for index, service_name in enumerate(SERVICE_NAMES):
        payload = _observed_headers_payload(service_name, tmp_path / f"name-{index}")
        lengths.append(len(payload))

    assert lengths == sorted(lengths), lengths
    assert all(first < second for first, second in itertools.pairwise(lengths)), lengths


def test_vmess_grpc_service_name_is_deterministic_across_runs(tmp_path: Path) -> None:
    first = len(_observed_headers_payload("fixture-service", tmp_path / "repeat-1"))
    second = len(_observed_headers_payload("fixture-service", tmp_path / "repeat-2"))

    assert first == second
