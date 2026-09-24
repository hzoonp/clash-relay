"""Validation against an actual Mihomo executable."""

from __future__ import annotations

import contextlib
import errno
import json
import os
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .redact import redact_text
from .util import dump_yaml, load_yaml_file


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run(command: list[str], *, cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            env={**os.environ, "TZ": "UTC"},
        )
    except OSError as exc:
        if exc.errno == errno.EACCES or getattr(exc, "winerror", None) == 193:
            raise ValidationError("Mihomo binary is not executable") from exc
        raise ValidationError("failed to execute Mihomo validation") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValidationError("failed to execute Mihomo validation") from exc


def _validation_copy(config_path: Path, workdir: Path) -> Path:
    config = load_yaml_file(config_path)
    if not isinstance(config, dict):
        raise ValidationError("candidate is not a YAML mapping")
    config = dict(config)
    config["mixed-port"] = _free_port()
    config["external-controller"] = f"127.0.0.1:{_free_port()}"
    config["secret"] = "clash-relay-validation-only"
    dns = dict(config.get("dns", {}))
    if dns.get("enable"):
        dns["listen"] = f"127.0.0.1:{_free_port()}"
    config["dns"] = dns
    target = workdir / "validation.yaml"
    target.write_text(dump_yaml(config), encoding="utf-8")
    return target


def _startup_smoke_copy(validation_path: Path, workdir: Path) -> tuple[Path, bool]:
    config = load_yaml_file(validation_path)
    if not isinstance(config, dict):
        raise ValidationError("validation candidate is not a YAML mapping")
    config = dict(config)
    tun = config.get("tun")
    tun_disabled = False
    if isinstance(tun, dict) and tun.get("enable") is True:
        startup_tun = dict(tun)
        startup_tun["enable"] = False
        config["tun"] = startup_tun
        tun_disabled = True
    target = workdir / "startup-smoke.yaml"
    target.write_text(dump_yaml(config), encoding="utf-8")
    return target, tun_disabled


def validate_with_mihomo(
    binary: Path,
    config_path: Path,
    *,
    startup_seconds: float = 1.5,
    config_test_timeout: float = 30.0,
    secret_values: tuple[str, ...] = (),
) -> dict[str, Any]:
    binary = binary.resolve()
    config_path = config_path.resolve()
    if not binary.is_file():
        raise ValidationError(f"Mihomo binary does not exist: {binary}")
    if not os.access(binary, os.X_OK):
        raise ValidationError(f"Mihomo binary is not executable: {binary}")
    with tempfile.TemporaryDirectory(prefix="clash-relay-mihomo-") as temp_name:
        workdir = Path(temp_name)
        validation_path = _validation_copy(config_path, workdir)
        test = _run(
            [str(binary), "-t", "-d", str(workdir), "-f", str(validation_path)],
            cwd=workdir,
            timeout=config_test_timeout,
        )
        if test.returncode != 0:
            output = redact_text(test.stdout[-5000:], secret_values)
            raise ValidationError(f"Mihomo configuration test failed: {output}")
        startup_path, startup_tun_disabled = _startup_smoke_copy(validation_path, workdir)
        command = [str(binary), "-d", str(workdir), "-f", str(startup_path)]
        try:
            process = subprocess.Popen(
                command,
                cwd=workdir,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env={**os.environ, "TZ": "UTC"},
                start_new_session=True,
            )
        except OSError as exc:
            raise ValidationError("failed to start Mihomo smoke process") from exc
        try:
            deadline = time.monotonic() + startup_seconds
            while time.monotonic() < deadline:
                returncode = process.poll()
                if returncode is not None:
                    output = process.stdout.read() if process.stdout else ""
                    output = redact_text(output[-5000:], secret_values)
                    raise ValidationError(
                        f"Mihomo exited during startup smoke with {returncode}: {output}"
                    )
                time.sleep(0.05)
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    process.terminate()
                else:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        process.kill()
                    else:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
    version = _run([str(binary), "-v"], cwd=config_path.parent, timeout=10)
    version_line = (version.stdout.strip().splitlines() or [binary.name])[0]
    return {
        "binary": binary.name,
        "version": version_line,
        "config_test": "passed",
        "startup_smoke": "passed",
        "startup_tun_disabled": startup_tun_disabled,
    }


def load_candidate(path: Path) -> dict[str, Any]:
    document = load_yaml_file(path)
    if not isinstance(document, dict):
        raise ValidationError("candidate must be a YAML mapping")
    # Round-trip via JSON catches non-plain YAML scalar objects.
    try:
        json.dumps(document)
    except TypeError as exc:
        raise ValidationError("candidate contains non-JSON-compatible values") from exc
    return document
