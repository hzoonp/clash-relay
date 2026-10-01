from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Any

import pytest

import clash_relay.mihomo_probe_runtime as runtime


class _FakeProcess:
    def __init__(self, *, returncode: int | None = None, timeout_once: bool = False) -> None:
        self.pid = 12345
        self.returncode = returncode
        self.timeout_once = timeout_once
        self.wait_calls = 0
        self.terminated = 0
        self.killed = 0

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        if self.timeout_once and self.wait_calls == 1:
            raise subprocess.TimeoutExpired("mihomo", timeout)
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.terminated += 1

    def kill(self) -> None:
        self.killed += 1


def test_free_tcp_port_returns_bindable_port() -> None:
    port = runtime.free_tcp_port()
    assert isinstance(port, int)
    assert 0 < port < 65536


def test_wait_for_process_condition_succeeds_without_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess()
    sleeps: list[float] = []
    monkeypatch.setattr(runtime.time, "sleep", sleeps.append)

    runtime.wait_for_process_condition(
        process,  # type: ignore[arg-type]
        lambda: True,
        timeout=1.0,
    )

    assert sleeps == []


def test_ensure_process_running_reports_early_exit() -> None:
    process = _FakeProcess(returncode=9)
    with pytest.raises(runtime.MihomoProcessExited, match="status 9"):
        runtime.ensure_process_running(process)  # type: ignore[arg-type]


def test_wait_for_process_condition_reports_early_exit() -> None:
    process = _FakeProcess(returncode=7)
    with pytest.raises(runtime.MihomoProcessExited, match="status 7"):
        runtime.wait_for_process_condition(
            process,  # type: ignore[arg-type]
            lambda: False,
            timeout=1.0,
        )


def test_wait_for_process_condition_ignores_declared_transient_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess()
    calls = 0

    def probe() -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("not ready")
        return True

    monkeypatch.setattr(runtime.time, "sleep", lambda _value: None)
    runtime.wait_for_process_condition(
        process,  # type: ignore[arg-type]
        probe,
        timeout=1.0,
        transient_errors=(ConnectionError,),
    )

    assert calls == 2


def test_wait_for_process_condition_times_out() -> None:
    process = _FakeProcess()
    with pytest.raises(runtime.MihomoReadinessTimeout):
        runtime.wait_for_process_condition(
            process,  # type: ignore[arg-type]
            lambda: False,
            timeout=0.0,
        )


def test_stop_mihomo_process_escalates_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess(timeout_once=True)
    signals: list[int] = []

    if os.name == "nt":
        runtime.stop_mihomo_process(process, timeout=0.01)  # type: ignore[arg-type]
        assert process.terminated == 1
        assert process.killed == 1
    else:
        monkeypatch.setattr(runtime.os, "killpg", lambda _pid, sig: signals.append(sig))
        runtime.stop_mihomo_process(process, timeout=0.01)  # type: ignore[arg-type]
        assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert process.wait_calls == 2


def test_start_mihomo_process_uses_isolated_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    process = _FakeProcess()

    def fake_popen(command: list[str], **kwargs: Any):
        captured["command"] = command
        captured.update(kwargs)
        return process

    monkeypatch.setattr(runtime.subprocess, "Popen", fake_popen)
    binary = tmp_path / "mihomo"
    config = tmp_path / "probe.yaml"

    result = runtime.start_mihomo_process(binary, config, tmp_path)

    assert result is process
    assert captured["command"] == [
        str(binary),
        "-d",
        str(tmp_path),
        "-f",
        str(config),
    ]
    assert captured["cwd"] == tmp_path
    assert captured["env"]["TZ"] == "UTC"
    assert captured["start_new_session"] is True


def test_run_config_test_uses_isolated_workdir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any):
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    binary = tmp_path / "mihomo"
    config = tmp_path / "probe.yaml"

    result = runtime.run_config_test(binary, config, tmp_path)

    assert result.returncode == 0
    assert captured["command"] == [
        str(binary),
        "-t",
        "-d",
        str(tmp_path),
        "-f",
        str(config),
    ]
    assert captured["cwd"] == tmp_path
    assert captured["env"]["TZ"] == "UTC"
