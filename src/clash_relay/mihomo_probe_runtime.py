"""Shared Mihomo process primitives for production qualification probes."""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import time
from collections.abc import Callable
from pathlib import Path


class MihomoProcessExited(RuntimeError):
    """Mihomo exited before a runtime readiness condition was satisfied."""


class MihomoReadinessTimeout(RuntimeError):
    """A Mihomo runtime readiness condition did not become true in time."""


def free_tcp_port() -> int:
    """Reserve a currently free loopback TCP port for a short-lived probe."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run_config_test(
    binary: Path,
    config_path: Path,
    workdir: Path,
    *,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[bytes]:
    """Run Mihomo's config test without interpreting service-level validity."""

    return subprocess.run(
        [str(binary), "-t", "-d", str(workdir), "-f", str(config_path)],
        cwd=workdir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
        env={**os.environ, "TZ": "UTC"},
    )


def start_mihomo_process(
    binary: Path,
    config_path: Path,
    workdir: Path,
) -> subprocess.Popen[bytes]:
    """Start one isolated Mihomo process in its own process group."""

    return subprocess.Popen(
        [str(binary), "-d", str(workdir), "-f", str(config_path)],
        cwd=workdir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        env={**os.environ, "TZ": "UTC"},
        start_new_session=True,
    )


def stop_mihomo_process(
    process: subprocess.Popen[bytes],
    *,
    timeout: float = 5.0,
) -> None:
    """Stop Mihomo deterministically, escalating from TERM to KILL."""

    if process.poll() is not None:
        process.wait(timeout=timeout)
        return

    if os.name == "nt":
        process.terminate()
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass

    if os.name == "nt":
        process.kill()
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=timeout)


def wait_for_process_condition(
    process: subprocess.Popen[bytes],
    probe: Callable[[], bool],
    *,
    timeout: float,
    interval: float = 0.1,
    transient_errors: tuple[type[BaseException], ...] = (),
) -> None:
    """Wait for one readiness predicate while requiring Mihomo to stay alive."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        returncode = process.poll()
        if returncode is not None:
            raise MihomoProcessExited(f"Mihomo exited with status {returncode}")
        try:
            if probe():
                return
        except transient_errors:
            pass
        time.sleep(interval)
    raise MihomoReadinessTimeout("Mihomo readiness condition timed out")
