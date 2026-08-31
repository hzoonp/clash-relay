"""Privacy-preserving diagnostics for temporary Mihomo qualification configs."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from .browsing_qualification import (
    _browsing_provider_payloads,
    _free_port,
    _temporary_probe_config,
)
from .util import dump_yaml, load_yaml_file

_MAX_DIAGNOSTIC_CHARS = 1200
_IPV4_RE = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
_LONG_TOKEN_RE = re.compile(r"(?i)\b[a-f0-9]{24,}\b")
_INVALID_PAYLOAD_FIELD_RE = re.compile(
    r"\b(?:filed|field) payload\[(\d+)\]\[([A-Za-z0-9_-]{1,64})\] invalid\b"
)
_PROVIDER_ERROR_RE = re.compile(r"\bparse proxy provider ([A-Za-z0-9_-]{1,128}) error:")


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


def _value_shape(value: Any) -> dict[str, object]:
    if isinstance(value, dict):
        counts = Counter(type(item).__name__ for item in value.values())
        return {
            "type": "mapping",
            "items": len(value),
            "value_types": dict(sorted(counts.items())),
        }
    if isinstance(value, list):
        counts = Counter(type(item).__name__ for item in value)
        return {
            "type": "list",
            "items": len(value),
            "value_types": dict(sorted(counts.items())),
        }
    if value is None:
        return {"type": "null"}
    return {"type": type(value).__name__}


def _safe_structural_detail(
    raw_output: str,
    candidate: dict[str, Any],
) -> dict[str, object]:
    """Extract only core schema identifiers and Python type shapes, never values."""
    field_match = _INVALID_PAYLOAD_FIELD_RE.search(raw_output)
    provider_match = _PROVIDER_ERROR_RE.search(raw_output)
    if field_match is None or provider_match is None:
        return {}
    index = int(field_match.group(1))
    field = field_match.group(2)
    provider_name = provider_match.group(1)
    providers = candidate.get("proxy-providers")
    provider = providers.get(provider_name) if isinstance(providers, dict) else None
    payload = provider.get("payload") if isinstance(provider, dict) else None
    if not isinstance(payload, list) or not 0 <= index < len(payload):
        return {
            "provider": provider_name,
            "payload_index": index,
            "invalid_field": field,
        }
    proxy = payload[index]
    if not isinstance(proxy, dict):
        return {
            "provider": provider_name,
            "payload_index": index,
            "invalid_field": field,
            "field_shape": {"type": type(proxy).__name__},
        }
    return {
        "provider": provider_name,
        "payload_index": index,
        "invalid_field": field,
        "field_shape": _value_shape(proxy.get(field)),
    }


def diagnose_browsing_core(candidate_path: Path, mihomo_bin: Path) -> dict[str, object]:
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
        **_safe_structural_detail(result.stdout, candidate),
        "diagnostic": redact_core_output(result.stdout, candidate),
    }
