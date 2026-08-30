from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml


pytestmark = pytest.mark.integration


def _binary() -> Path:
    value = os.environ.get("MIHOMO_BIN")
    if not value:
        pytest.skip("MIHOMO_BIN is not set")
    return Path(value).resolve()


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _api(url: str, secret: str):  # noqa: ANN202
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {secret}"})
    with urllib.request.urlopen(request, timeout=1) as response:
        return json.load(response)


def test_provider_health_check_uses_head_and_accepts_401(tmp_path: Path) -> None:
    observed: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self) -> None:  # noqa: N802
            observed.append(("HEAD", self.path))
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            observed.append(("GET", self.path))
            self.send_response(405)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args) -> None:  # noqa: A002, ANN001
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    controller_port = _port()
    secret = "integration-only-secret"
    config = {
        "mixed-port": _port(),
        "allow-lan": False,
        "mode": "rule",
        "log-level": "warning",
        "external-controller": f"127.0.0.1:{controller_port}",
        "secret": secret,
        "proxy-providers": {
            "head-check": {
                "type": "inline",
                "health-check": {
                    "enable": True,
                    "url": f"http://127.0.0.1:{server.server_port}/probe",
                    "interval": 1,
                    "timeout": 1000,
                    "lazy": False,
                    "expected-status": 401,
                },
                "payload": [{"name": "local-direct", "type": "direct"}],
            }
        },
        "proxy-groups": [
            {"name": "Head Test", "type": "select", "use": ["head-check"]}
        ],
        "rules": ["MATCH,Head Test"],
    }
    path = tmp_path / "head.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    process = subprocess.Popen(
        [str(_binary()), "-d", str(tmp_path), "-f", str(path)],
        cwd=tmp_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 15
        provider = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                pytest.fail(f"Mihomo exited during HEAD test: {output}")
            try:
                provider = _api(
                    f"http://127.0.0.1:{controller_port}/providers/proxies/head-check",
                    secret,
                )
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(0.1)
                continue
            proxies = provider.get("proxies", [])
            if observed and proxies and proxies[0].get("alive") is True:
                break
            time.sleep(0.1)
        assert ("HEAD", "/probe") in observed
        assert ("GET", "/probe") not in observed
        assert provider is not None
        assert provider["proxies"][0]["alive"] is True
    finally:
        server.shutdown()
        server.server_close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
