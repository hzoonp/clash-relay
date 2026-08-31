from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from clash_relay.browsing_runtime import (
    BROWSING_AUTO_GROUP,
    BROWSING_PUBLIC_GROUP,
    BROWSING_RESERVE_GROUP,
    BROWSING_STABLE_GROUP,
)

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


def _api(controller_port: int, secret: str, group_name: str) -> dict:
    encoded = urllib.parse.quote(group_name, safe="")
    request = urllib.request.Request(
        f"http://127.0.0.1:{controller_port}/proxies/{encoded}",
        headers={"Authorization": f"Bearer {secret}"},
    )
    with urllib.request.urlopen(request, timeout=1) as response:
        payload = json.load(response)
    assert isinstance(payload, dict)
    return payload


def _wait_controller(process: subprocess.Popen, controller_port: int, secret: str) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            pytest.fail(f"Mihomo exited before controller became ready: {output}")
        try:
            _api(controller_port, secret, BROWSING_PUBLIC_GROUP)
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.1)
    pytest.fail("Mihomo controller did not become ready")


def test_real_mihomo_hides_browsing_nodes_and_fails_over_to_reserve(tmp_path: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        def _ok(self) -> None:
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_HEAD = _ok
        do_GET = _ok

        def log_message(self, format, *args) -> None:
            return

    probe_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    probe_thread = threading.Thread(target=probe_server.serve_forever, daemon=True)
    probe_thread.start()

    controller_port = _port()
    broken_port = _port()
    secret = "browsing-runtime-hardening-integration"
    probe_url = f"http://127.0.0.1:{probe_server.server_port}/generate_204"
    candidate = {
        "mixed-port": _port(),
        "allow-lan": False,
        "mode": "rule",
        "log-level": "warning",
        "external-controller": f"127.0.0.1:{controller_port}",
        "secret": secret,
        "proxy-providers": {
            "cr_browsing_any": {
                "type": "inline",
                "payload": [
                    {
                        "name": "Stable Broken",
                        "type": "http",
                        "server": "127.0.0.1",
                        "port": broken_port,
                    },
                    {"name": "Reserve Direct", "type": "direct"},
                ],
            }
        },
        "proxy-groups": [
            {
                "name": BROWSING_STABLE_GROUP,
                "type": "url-test",
                "hidden": True,
                "use": ["cr_browsing_any"],
                "filter": "^Stable Broken$",
                "url": probe_url,
                "interval": 1,
                "timeout": 1000,
                "lazy": False,
                "expected-status": 204,
                "tolerance": 50,
            },
            {
                "name": BROWSING_RESERVE_GROUP,
                "type": "url-test",
                "hidden": True,
                "use": ["cr_browsing_any"],
                "filter": "^Reserve Direct$",
                "url": probe_url,
                "interval": 1,
                "timeout": 1000,
                "lazy": False,
                "expected-status": 204,
                "tolerance": 50,
            },
            {
                "name": BROWSING_AUTO_GROUP,
                "type": "fallback",
                "hidden": True,
                "proxies": [BROWSING_STABLE_GROUP, BROWSING_RESERVE_GROUP],
                "url": probe_url,
                "interval": 1,
                "timeout": 1000,
                "lazy": False,
                "expected-status": 204,
            },
            {
                "name": BROWSING_PUBLIC_GROUP,
                "type": "select",
                "proxies": [BROWSING_AUTO_GROUP, "DIRECT"],
            },
        ],
        "rules": [f"MATCH,{BROWSING_PUBLIC_GROUP}"],
    }
    path = tmp_path / "browsing-runtime.yaml"
    path.write_text(yaml.safe_dump(candidate, sort_keys=False), encoding="utf-8")

    process = subprocess.Popen(
        [str(_binary()), "-d", str(tmp_path), "-f", str(path)],
        cwd=tmp_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env={**os.environ, "TZ": "UTC"},
    )
    try:
        _wait_controller(process, controller_port, secret)
        public = _api(controller_port, secret, BROWSING_PUBLIC_GROUP)
        assert public.get("all") == [BROWSING_AUTO_GROUP, "DIRECT"]
        assert "Stable Broken" not in public.get("all", [])
        assert "Reserve Direct" not in public.get("all", [])

        deadline = time.monotonic() + 15
        last_auto: dict = {}
        last_stable: dict = {}
        last_reserve: dict = {}
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                pytest.fail(f"Mihomo exited during browsing failover test: {output}")
            last_auto = _api(controller_port, secret, BROWSING_AUTO_GROUP)
            last_stable = _api(controller_port, secret, BROWSING_STABLE_GROUP)
            last_reserve = _api(controller_port, secret, BROWSING_RESERVE_GROUP)
            if (
                last_auto.get("now") == BROWSING_RESERVE_GROUP
                and last_reserve.get("now") == "Reserve Direct"
            ):
                break
            time.sleep(0.2)

        assert last_stable.get("all") == ["Stable Broken"]
        assert last_reserve.get("all") == ["Reserve Direct"]
        assert last_reserve.get("now") == "Reserve Direct"
        assert last_auto.get("all") == [BROWSING_STABLE_GROUP, BROWSING_RESERVE_GROUP]
        assert last_auto.get("now") == BROWSING_RESERVE_GROUP, {
            "auto": last_auto,
            "stable": last_stable,
            "reserve": last_reserve,
        }
    finally:
        probe_server.shutdown()
        probe_server.server_close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
