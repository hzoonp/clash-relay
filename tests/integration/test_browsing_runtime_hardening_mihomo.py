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

from clash_relay.browsing_regions import (
    region_display_name,
    region_reserve_group,
    region_stable_group,
)
from clash_relay.browsing_runtime import BROWSING_AUTO_GROUP, BROWSING_PUBLIC_GROUP

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


def _regional_candidate(
    *,
    controller_port: int,
    secret: str,
    probe_url: str,
    us_reserve_direct: bool,
) -> dict:
    us_stable_port = _port()
    us_reserve_port = _port()
    us_reserve = (
        {"name": "US Reserve Direct", "type": "direct"}
        if us_reserve_direct
        else {
            "name": "US Reserve Broken",
            "type": "http",
            "server": "127.0.0.1",
            "port": us_reserve_port,
        }
    )
    return {
        "mixed-port": _port(),
        "allow-lan": False,
        "mode": "rule",
        "log-level": "warning",
        "external-controller": f"127.0.0.1:{controller_port}",
        "secret": secret,
        "proxy-providers": {
            "cr_browsing_us": {
                "type": "inline",
                "payload": [
                    {
                        "name": "US Stable Broken",
                        "type": "http",
                        "server": "127.0.0.1",
                        "port": us_stable_port,
                    },
                    us_reserve,
                ],
            },
            "cr_browsing_jp": {
                "type": "inline",
                "payload": [
                    {"name": "JP Stable Direct", "type": "direct"},
                    {"name": "JP Reserve Direct", "type": "direct"},
                ],
            },
        },
        "proxy-groups": [
            {
                "name": region_stable_group("US"),
                "type": "url-test",
                "hidden": True,
                "use": ["cr_browsing_us"],
                "filter": "^US Stable Broken$",
                "url": probe_url,
                "interval": 1,
                "timeout": 1000,
                "lazy": False,
                "expected-status": 204,
                "tolerance": 50,
            },
            {
                "name": region_reserve_group("US"),
                "type": "url-test",
                "hidden": True,
                "use": ["cr_browsing_us"],
                "filter": "^US Reserve (Direct|Broken)$",
                "url": probe_url,
                "interval": 1,
                "timeout": 1000,
                "lazy": False,
                "expected-status": 204,
                "tolerance": 50,
            },
            {
                "name": region_display_name("US"),
                "type": "fallback",
                "hidden": True,
                "proxies": [region_stable_group("US"), region_reserve_group("US")],
                "url": probe_url,
                "interval": 1,
                "timeout": 1000,
                "lazy": False,
                "expected-status": 204,
            },
            {
                "name": region_stable_group("JP"),
                "type": "url-test",
                "hidden": True,
                "use": ["cr_browsing_jp"],
                "filter": "^JP Stable Direct$",
                "url": probe_url,
                "interval": 1,
                "timeout": 1000,
                "lazy": False,
                "expected-status": 204,
                "tolerance": 50,
            },
            {
                "name": region_reserve_group("JP"),
                "type": "url-test",
                "hidden": True,
                "use": ["cr_browsing_jp"],
                "filter": "^JP Reserve Direct$",
                "url": probe_url,
                "interval": 1,
                "timeout": 1000,
                "lazy": False,
                "expected-status": 204,
                "tolerance": 50,
            },
            {
                "name": region_display_name("JP"),
                "type": "fallback",
                "hidden": True,
                "proxies": [region_stable_group("JP"), region_reserve_group("JP")],
                "url": probe_url,
                "interval": 1,
                "timeout": 1000,
                "lazy": False,
                "expected-status": 204,
            },
            {
                "name": BROWSING_AUTO_GROUP,
                "type": "fallback",
                "hidden": True,
                "proxies": [region_display_name("US"), region_display_name("JP")],
                "url": probe_url,
                "interval": 1,
                "timeout": 1000,
                "lazy": False,
                "expected-status": 204,
            },
            {
                "name": BROWSING_PUBLIC_GROUP,
                "type": "select",
                "proxies": [
                    BROWSING_AUTO_GROUP,
                    region_display_name("US"),
                    region_display_name("JP"),
                    "DIRECT",
                ],
            },
        ],
        "rules": [f"MATCH,{BROWSING_PUBLIC_GROUP}"],
    }


def _run_and_wait(
    tmp_path: Path,
    *,
    candidate: dict,
    controller_port: int,
    secret: str,
    expected_auto: str,
    expected_us_tier: str | None,
) -> None:
    path = tmp_path / "browsing-regional-runtime.yaml"
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
        assert public.get("all") == [
            BROWSING_AUTO_GROUP,
            region_display_name("US"),
            region_display_name("JP"),
            "DIRECT",
        ]
        for raw_name in (
            "US Stable Broken",
            "US Reserve Direct",
            "US Reserve Broken",
            "JP Stable Direct",
            "JP Reserve Direct",
        ):
            assert raw_name not in public.get("all", [])

        deadline = time.monotonic() + 20
        last_auto: dict = {}
        last_us: dict = {}
        last_jp: dict = {}
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                pytest.fail(f"Mihomo exited during regional browsing test: {output}")
            last_auto = _api(controller_port, secret, BROWSING_AUTO_GROUP)
            last_us = _api(controller_port, secret, region_display_name("US"))
            last_jp = _api(controller_port, secret, region_display_name("JP"))
            us_ok = expected_us_tier is None or last_us.get("now") == expected_us_tier
            if last_auto.get("now") == expected_auto and us_ok:
                break
            time.sleep(0.2)

        assert last_auto.get("all") == [region_display_name("US"), region_display_name("JP")]
        assert last_auto.get("now") == expected_auto, {
            "auto": last_auto,
            "us": last_us,
            "jp": last_jp,
        }
        assert last_us.get("all") == [region_stable_group("US"), region_reserve_group("US")]
        assert region_display_name("JP") not in last_us.get("all", [])
        if expected_us_tier is not None:
            assert last_us.get("now") == expected_us_tier
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)


def _probe_server() -> tuple[ThreadingHTTPServer, threading.Thread]:
    class Handler(BaseHTTPRequestHandler):
        def _ok(self) -> None:
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_HEAD = _ok
        do_GET = _ok

        def log_message(self, format, *args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_real_mihomo_prefers_same_region_reserve_before_next_region(tmp_path: Path) -> None:
    probe_server, _ = _probe_server()
    controller_port = _port()
    secret = "regional-browsing-same-region-reserve"
    try:
        candidate = _regional_candidate(
            controller_port=controller_port,
            secret=secret,
            probe_url=f"http://127.0.0.1:{probe_server.server_port}/generate_204",
            us_reserve_direct=True,
        )
        _run_and_wait(
            tmp_path,
            candidate=candidate,
            controller_port=controller_port,
            secret=secret,
            expected_auto=region_display_name("US"),
            expected_us_tier=region_reserve_group("US"),
        )
    finally:
        probe_server.shutdown()
        probe_server.server_close()


def test_real_mihomo_crosses_region_only_when_preferred_region_is_unavailable(
    tmp_path: Path,
) -> None:
    probe_server, _ = _probe_server()
    controller_port = _port()
    secret = "regional-browsing-cross-region-fallback"
    try:
        candidate = _regional_candidate(
            controller_port=controller_port,
            secret=secret,
            probe_url=f"http://127.0.0.1:{probe_server.server_port}/generate_204",
            us_reserve_direct=False,
        )
        _run_and_wait(
            tmp_path,
            candidate=candidate,
            controller_port=controller_port,
            secret=secret,
            expected_auto=region_display_name("JP"),
            expected_us_tier=None,
        )
    finally:
        probe_server.shutdown()
        probe_server.server_close()
