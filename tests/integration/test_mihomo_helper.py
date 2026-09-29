from __future__ import annotations

import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from _mihomo import RunningMihomo, serving_http, stop_server, wait_http_ready, wait_until


class _Health(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ready")

    def log_message(self, _format: str, *_args: object) -> None:
        pass


def test_http_readiness_waits_for_server_thread() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Health)
    thread = Thread(target=server.serve_forever)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            waiting = pool.submit(
                wait_http_ready, "127.0.0.1", server.server_port, thread, timeout=2.0
            )
            assert not waiting.done()
            thread.start()
            waiting.result(timeout=3)
    finally:
        if thread.is_alive():
            stop_server(server, thread)
        else:
            server.server_close()


def test_http_readiness_reports_unstarted_server_state() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Health)
    thread = Thread(target=server.serve_forever)
    try:
        with pytest.raises(pytest.fail.Exception) as failure:
            wait_http_ready("127.0.0.1", server.server_port, thread, timeout=0.15)
        message = str(failure.value)
        assert f"127.0.0.1:{server.server_port}" in message
        assert "0.15s" in message
        assert "thread_alive=False" in message
    finally:
        server.server_close()


def test_server_teardown_releases_port() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Health)
    thread = Thread(target=server.serve_forever)
    port = server.server_port
    with serving_http(server, thread):
        wait_http_ready("127.0.0.1", port, thread)
    assert not thread.is_alive()
    with socket.socket() as rebound:
        rebound.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        rebound.bind(("127.0.0.1", port))


def test_mihomo_readiness_reports_early_exit_and_log_tail(tmp_path: Path) -> None:
    log_path = tmp_path / "mihomo.log"
    stream = log_path.open("wb")
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; print('fixture-exit-marker', flush=True); sys.exit(7)"],
        stdout=stream,
        stderr=subprocess.STDOUT,
    )
    running = RunningMihomo(process=process, log_path=log_path, log_stream=stream)
    try:
        process.wait(timeout=3)
        with pytest.raises(pytest.fail.Exception) as failure:
            wait_until(lambda: False, timeout=1, label="fixture Mihomo", mihomo=running)
        message = str(failure.value)
        assert "Mihomo exited before readiness" in message
        assert "returncode=7" in message
        assert "fixture-exit-marker" in message
    finally:
        running.stop()
