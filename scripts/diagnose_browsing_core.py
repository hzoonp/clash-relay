from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from clash_relay.browsing_qualification import (
    _browsing_provider_payloads,
    _free_port,
    _temporary_probe_config,
)
from clash_relay.util import dump_yaml, load_yaml_file

_MAX_DIAGNOSTIC_CHARS = 1200
_IPV4_RE = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
_LONG_TOKEN_RE = re.compile(r"(?i)\b[a-f0-9]{24,}\b")


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _collect_string_values(value: Any, target: set[str]) -> None:
    if isinstance(value, str):
        if value:
            target.add(value)
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_string_values(item, target)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_string_values(item, target)


def redact_core_output(text: str, candidate: dict[str, Any]) -> str:
    """Remove candidate-derived proxy secrets and identifiers from Mihomo output."""
    sensitive: set[str] = set()
    _collect_string_values(candidate.get("proxy-providers", {}), sensitive)
    cleaned = text
    for value in sorted(sensitive, key=len, reverse=True):
        if len(value) >= 2:
            cleaned = cleaned.replace(value, "<redacted>")
    cleaned = _IPV4_RE.sub("<address>", cleaned)
    cleaned = _LONG_TOKEN_RE.sub("<token>", cleaned)
    cleaned = cleaned.replace("\r", " ").replace("\n", " ")
    cleaned = " ".join(cleaned.split())
    return cleaned[:_MAX_DIAGNOSTIC_CHARS]


def diagnose(candidate_path: Path, mihomo_bin: Path) -> dict[str, object]:
    candidate = load_yaml_file(candidate_path)
    if not isinstance(candidate, dict):
        return {"status": "unavailable", "reason": "candidate_not_mapping"}
    provider_payloads = _browsing_provider_payloads(candidate)

    with tempfile.TemporaryDirectory(prefix="clash-relay-browsing-diagnostic-") as temp_name:
        workdir = Path(temp_name)
        probe = _temporary_probe_config(
            candidate,
            provider_payloads,
            mixed_port=_free_port(),
            controller_port=_free_port(),
            secret="clash-relay-browsing-diagnostic-only",
        )
        probe_path = workdir / "probe.yaml"
        probe_path.write_text(dump_yaml(probe), encoding="utf-8")
        result = subprocess.run(
            [str(mihomo_bin.resolve()), "-t", "-d", str(workdir), "-f", str(probe_path)],
            cwd=workdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
            check=False,
            env={**os.environ, "TZ": "UTC"},
        )

    if result.returncode == 0:
        return {"status": "valid_on_retry", "returncode": 0}
    return {
        "status": "rejected",
        "returncode": result.returncode,
        "diagnostic": redact_core_output(result.stdout, candidate),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Emit a redacted structural diagnostic for browsing qualification config failures."
    )
    parser.add_argument("--candidate", type=_path, required=True)
    parser.add_argument("--mihomo-bin", type=_path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = diagnose(args.candidate, args.mihomo_bin)
    except Exception as exc:  # diagnostic path must never mask the original failure
        payload = {"status": "unavailable", "reason": type(exc).__name__}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
