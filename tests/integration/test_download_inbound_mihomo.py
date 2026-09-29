from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from clash_relay.util import dump_yaml

pytestmark = pytest.mark.integration


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _FileHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"download-inbound-reached")

    def log_message(self, _format: str, *_args: object) -> None:
        pass


def _request(proxy_port: int, server_port: int, host: str = "127.0.0.1") -> tuple[int, bytes]:
    connection = HTTPConnection("127.0.0.1", proxy_port, timeout=3)
    try:
        connection.request("GET", f"http://{host}:{server_port}/file.iso")
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_download_listener_overrides_earlier_web_domain_classification(tmp_path: Path) -> None:
    binary = os.environ.get("MIHOMO_BIN")
    if not binary:
        pytest.skip("MIHOMO_BIN is not set")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FileHandler)
    server_port = int(server.server_address[1])
    normal_port = _port()
    while normal_port == server_port:
        normal_port = _port()
    download_port = _port()
    while download_port in {server_port, normal_port}:
        download_port = _port()
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    config = {
        "mixed-port": normal_port,
        "listeners": [
            {"name": "download-in", "type": "mixed", "listen": "127.0.0.1", "port": download_port}
        ],
        "allow-lan": False,
        "hosts": {"assets.gvt1.com": "127.0.0.1", "www.google.com": "127.0.0.1"},
        "mode": "rule",
        "log-level": "silent",
        "proxy-groups": [
            {"name": "下载流量", "type": "select", "proxies": ["DIRECT"]},
            {"name": "网页浏览", "type": "select", "proxies": ["REJECT"]},
        ],
        "rules": [
            "IN-NAME,download-in,下载流量",
            "PROCESS-NAME,aria2c.exe,下载流量",
            "DOMAIN-SUFFIX,gvt1.com,下载流量",
            "DOMAIN-SUFFIX,google.com,网页浏览",
            "IP-CIDR,127.0.0.1/32,网页浏览,no-resolve",
            "MATCH,DIRECT",
        ],
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(dump_yaml(config), encoding="utf-8")
    process = subprocess.Popen(
        [binary, "-d", str(tmp_path), "-f", str(config_path)],
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail("Mihomo exited before download listener became ready")
            try:
                with socket.create_connection(("127.0.0.1", download_port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            pytest.fail("Mihomo download listener did not become ready")

        assert _request(normal_port, server_port)[0] != 200
        assert _request(download_port, server_port) == (200, b"download-inbound-reached")
        assert _request(normal_port, server_port, "assets.gvt1.com") == (
            200,
            b"download-inbound-reached",
        )
        assert _request(normal_port, server_port, "www.google.com")[0] != 200
        if os.name == "nt":
            downloader = tmp_path / "aria2c.exe"
            shutil.copy2(sys.executable, downloader)
            code = (
                "from http.client import HTTPConnection; import sys; "
                "c=HTTPConnection('127.0.0.1',int(sys.argv[1]),timeout=3); "
                "c.request('GET','http://127.0.0.1:'+sys.argv[2]+'/file.iso'); "
                "print(c.getresponse().status)"
            )
            result = subprocess.run(
                [str(downloader), "-c", code, str(normal_port), str(server_port)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            assert result.returncode == 0, result.stderr
            assert result.stdout.strip() == "200"
    finally:
        process.terminate()
        process.wait(timeout=5)
        server.shutdown()
        server.server_close()
