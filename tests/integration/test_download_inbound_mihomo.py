from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from _mihomo import mihomo_session, serving_http, wait_until

from clash_relay.util import dump_yaml

pytestmark = pytest.mark.integration
_BODY = b"download-inbound-reached"


def _port(*, excluded: set[int]) -> int:
    while True:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            value = int(sock.getsockname()[1])
        if value not in excluded:
            return value


class _FileHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(_BODY)

    def log_message(self, _format: str, *_args: object) -> None:
        pass


def _request(proxy_port: int, server_port: int, host: str = "127.0.0.1") -> tuple[int, bytes]:
    connection = HTTPConnection("127.0.0.1", proxy_port, timeout=2)
    try:
        connection.request("GET", f"http://{host}:{server_port}/file.iso")
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def _wait_proxy_route(mihomo, proxy_port: int, server_port: int) -> None:
    def probe() -> bool:
        response = _request(proxy_port, server_port)
        if response != (200, _BODY):
            raise AssertionError(f"download proxy probe returned {response!r}")
        return True

    wait_until(
        probe,
        timeout=12,
        label=f"download Mihomo route 127.0.0.1:{proxy_port} -> 127.0.0.1:{server_port}",
        mihomo=mihomo,
    )


def _listener(port: int) -> dict:
    return {
        "name": "download-in",
        "type": "mixed",
        "listen": "127.0.0.1",
        "port": port,
        "proxy": "下载流量",
    }


@pytest.mark.parametrize("_attempt", range(3))
def test_download_listener_overrides_earlier_web_domain_classification(
    tmp_path: Path, _attempt: int
) -> None:
    binary = os.environ.get("MIHOMO_BIN")
    if not binary:
        pytest.skip("MIHOMO_BIN is not set")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FileHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    with serving_http(server, thread):
        upstream_port = int(server.server_address[1])
        normal_port = _port(excluded={upstream_port})
        download_port = _port(excluded={upstream_port, normal_port})
        config = {
            "mixed-port": normal_port,
            "listeners": [_listener(download_port)],
            "allow-lan": False,
            "hosts": {
                "assets.gvt1.com": "127.0.0.1",
                "www.google.com": "127.0.0.1",
                "generic-proxylite.test": "127.0.0.1",
            },
            "mode": "rule",
            "log-level": "debug",
            "profile": {"store-selected": False},
            "proxy-groups": [
                {"name": "下载流量", "type": "select", "proxies": ["DIRECT"]},
                {"name": "网页浏览", "type": "select", "proxies": ["REJECT"]},
                {"name": "网页通用自动", "type": "select", "hidden": True, "proxies": ["DIRECT"]},
            ],
            "rule-providers": {
                "acl4ssr_proxy_lite": {
                    "type": "inline",
                    "behavior": "classical",
                    "payload": ["DOMAIN-SUFFIX,generic-proxylite.test"],
                }
            },
            "rules": [
                "PROCESS-NAME,aria2c.exe,下载流量",
                "DOMAIN-SUFFIX,gvt1.com,下载流量",
                "DOMAIN-SUFFIX,google.com,网页浏览",
                "RULE-SET,acl4ssr_proxy_lite,网页通用自动",
                "IP-CIDR,127.0.0.1/32,网页浏览,no-resolve",
                "MATCH,DIRECT",
            ],
        }
        workdir = tmp_path / "rule-mode"
        workdir.mkdir()
        path = workdir / "config.yaml"
        path.write_text(dump_yaml(config), encoding="utf-8")
        with mihomo_session(binary, path, workdir) as mihomo:
            _wait_proxy_route(mihomo, download_port, upstream_port)
            assert _request(normal_port, upstream_port)[0] != 200, mihomo.failure_context()
            assert _request(download_port, upstream_port) == (200, _BODY), mihomo.failure_context()
            assert _request(normal_port, upstream_port, "assets.gvt1.com") == (
                200,
                _BODY,
            ), mihomo.failure_context()
            assert _request(normal_port, upstream_port, "www.google.com")[0] != 200, (
                mihomo.failure_context()
            )
            assert _request(normal_port, upstream_port, "generic-proxylite.test") == (
                200,
                _BODY,
            ), mihomo.failure_context()
            if os.name == "nt":
                downloader = workdir / "aria2c.exe"
                shutil.copy2(sys.executable, downloader)
                code = (
                    "from http.client import HTTPConnection; import sys; "
                    "c=HTTPConnection('127.0.0.1',int(sys.argv[1]),timeout=3); "
                    "c.request('GET','http://127.0.0.1:'+sys.argv[2]+'/file.iso'); "
                    "print(c.getresponse().status)"
                )
                result = subprocess.run(
                    [str(downloader), "-c", code, str(normal_port), str(upstream_port)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                assert result.returncode == 0, result.stderr + mihomo.failure_context()
                assert result.stdout.strip() == "200", mihomo.failure_context()


@pytest.mark.parametrize("_attempt", range(3))
def test_download_listener_stays_bound_when_runtime_mode_is_global(
    tmp_path: Path, _attempt: int
) -> None:
    binary = os.environ.get("MIHOMO_BIN")
    if not binary:
        pytest.skip("MIHOMO_BIN is not set")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FileHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    with serving_http(server, thread):
        upstream_port = int(server.server_address[1])
        normal_port = _port(excluded={upstream_port})
        download_port = _port(excluded={upstream_port, normal_port})
        config = {
            "mixed-port": normal_port,
            "listeners": [_listener(download_port)],
            "mode": "global",
            "log-level": "debug",
            "profile": {"store-selected": False},
            "proxy-groups": [
                {"name": "GLOBAL", "type": "select", "proxies": ["REJECT"]},
                {"name": "下载流量", "type": "select", "proxies": ["DIRECT"]},
            ],
            "rules": ["MATCH,GLOBAL"],
        }
        workdir = tmp_path / "global-mode"
        workdir.mkdir()
        path = workdir / "config.yaml"
        path.write_text(dump_yaml(config), encoding="utf-8")
        with mihomo_session(binary, path, workdir) as mihomo:
            _wait_proxy_route(mihomo, download_port, upstream_port)
            assert _request(normal_port, upstream_port)[0] != 200, mihomo.failure_context()
            assert _request(download_port, upstream_port) == (200, _BODY), mihomo.failure_context()
