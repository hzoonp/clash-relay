"""Small readiness and lifecycle helpers for real Mihomo integration tests."""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread
from typing import BinaryIO

import pytest


@dataclass
class RunningMihomo:
    process: subprocess.Popen[bytes]
    log_path: Path
    log_stream: BinaryIO

    def log_tail(self, max_bytes: int = 8192) -> str:
        with self.log_path.open("rb") as source:
            source.seek(0, os.SEEK_END)
            source.seek(max(0, source.tell() - max_bytes))
            return source.read().decode("utf-8", errors="replace")

    def failure_context(self) -> str:
        return (
            f"Mihomo pid={self.process.pid} returncode={self.process.poll()} "
            f"log={self.log_path}\n--- Mihomo log tail ---\n{self.log_tail()}"
        )

    def stop(self) -> None:
        try:
            if self.process.poll() is None:
                if os.name == "nt":
                    self.process.terminate()
                else:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        self.process.kill()
                    else:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=5)
            else:
                self.process.wait(timeout=5)
        finally:
            self.log_stream.close()


def start_mihomo(binary: str | Path, config_path: Path, workdir: Path) -> RunningMihomo:
    workdir.mkdir(parents=True, exist_ok=True)
    log_path = workdir / "mihomo.log"
    stream = log_path.open("wb")
    try:
        process = subprocess.Popen(
            [str(binary), "-d", str(workdir), "-f", str(config_path)],
            cwd=workdir,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, "TZ": "UTC"},
        )
    except BaseException:
        stream.close()
        raise
    return RunningMihomo(process=process, log_path=log_path, log_stream=stream)


@contextlib.contextmanager
def mihomo_session(binary: str | Path, config_path: Path, workdir: Path) -> Iterator[RunningMihomo]:
    running = start_mihomo(binary, config_path, workdir)
    try:
        yield running
    finally:
        running.stop()


def wait_until(
    probe: Callable[[], bool],
    *,
    timeout: float,
    label: str | Callable[[], str],
    mihomo: RunningMihomo | None = None,
) -> None:
    def current_label() -> str:
        return label() if callable(label) else label

    deadline = time.monotonic() + timeout
    last_failure = "probe has not run"
    while time.monotonic() < deadline:
        if mihomo is not None and mihomo.process.poll() is not None:
            pytest.fail(
                f"{current_label()}: Mihomo exited before readiness. {mihomo.failure_context()}"
            )
        try:
            if probe():
                return
            last_failure = "probe returned false"
        except (OSError, AssertionError) as exc:
            last_failure = f"{type(exc).__name__}: {exc}"
        time.sleep(0.05)
    context = f"\n{mihomo.failure_context()}" if mihomo is not None else ""
    pytest.fail(
        f"{current_label()}: readiness timed out after {timeout}s; last={last_failure}{context}"
    )


def wait_http_ready(
    host: str,
    port: int,
    thread: Thread,
    *,
    expected_status: int = 200,
    timeout: float = 5.0,
) -> None:
    def probe() -> bool:
        if not thread.is_alive():
            raise AssertionError("server thread exited")
        connection = HTTPConnection(host, port, timeout=0.5)
        try:
            connection.request("GET", "/__health")
            response = connection.getresponse()
            response.read()
            return response.status == expected_status
        finally:
            connection.close()

    wait_until(
        probe,
        timeout=timeout,
        label=lambda: f"HTTP upstream {host}:{port} (thread_alive={thread.is_alive()})",
    )


def wait_udp_echo_ready(host: str, port: int, thread: Thread, *, timeout: float = 5.0) -> None:
    def probe() -> bool:
        if not thread.is_alive():
            raise AssertionError("UDP server thread exited")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(0.5)
            client.sendto(b"udp-health", (host, port))
            data, _ = client.recvfrom(128)
            return data == b"udp-health"

    wait_until(
        probe,
        timeout=timeout,
        label=lambda: f"UDP upstream {host}:{port} (thread_alive={thread.is_alive()})",
    )


def stop_server(server: object, thread: Thread) -> None:
    server.shutdown()  # type: ignore[attr-defined]
    server.server_close()  # type: ignore[attr-defined]
    thread.join(timeout=5)
    if thread.is_alive():
        pytest.fail("upstream server thread did not exit after shutdown")


@contextlib.contextmanager
def serving_http(server: object, thread: Thread, *, expected_status: int = 200) -> Iterator[None]:
    thread.start()
    try:
        wait_http_ready(
            "127.0.0.1",
            server.server_port,
            thread,
            expected_status=expected_status,  # type: ignore[attr-defined]
        )
        yield
    finally:
        stop_server(server, thread)


@contextlib.contextmanager
def serving_udp(server: object, thread: Thread) -> Iterator[None]:
    thread.start()
    try:
        wait_udp_echo_ready("127.0.0.1", server.server_address[1], thread)  # type: ignore[attr-defined]
        yield
    finally:
        stop_server(server, thread)
