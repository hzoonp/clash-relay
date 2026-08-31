"""Privacy-preserving historical stability for browsing auto-selection."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .util import atomic_write, dump_yaml, load_yaml_file

_STATE_VERSION = 1
_DOMAIN = b"clash-relay/browsing-scheduler-history/v1"
_ALPHA = 0.30
_MIN_HISTORY_RUNS = 2
_MIN_SUCCESS_EMA = 0.80
_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
_MAX_RECORDS = 4096
_MIN_PREFERRED_AUTO_NODES = 3
_BROWSING_PROVIDER_PREFIX = "cr_browsing_"
_RE2_META = frozenset("\\.+*?()|[]{}^$")


def derive_fingerprint_key(token: str) -> bytes:
    if not token:
        raise ValidationError("scheduler history requires a non-empty private key source")
    return hmac.new(token.encode("utf-8"), _DOMAIN, hashlib.sha256).digest()


def fingerprint_runtime_name(runtime_name: str, key: bytes) -> str:
    if not runtime_name or not key:
        raise ValidationError("scheduler history fingerprint input is invalid")
    return hmac.new(key, runtime_name.encode("utf-8"), hashlib.sha256).hexdigest()


def empty_history() -> dict[str, Any]:
    return {"version": _STATE_VERSION, "nodes": {}}


def parse_history_bytes(content: bytes | None) -> tuple[dict[str, Any], str]:
    """Parse auxiliary state; malformed or missing state safely degrades to empty history."""
    if not content:
        return empty_history(), "missing"
    try:
        document = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return empty_history(), "invalid"
    if not isinstance(document, dict) or document.get("version") != _STATE_VERSION:
        return empty_history(), "invalid"
    nodes = document.get("nodes")
    if not isinstance(nodes, dict):
        return empty_history(), "invalid"
    clean: dict[str, dict[str, Any]] = {}
    for fingerprint, record in nodes.items():
        if not isinstance(fingerprint, str) or len(fingerprint) != 64:
            return empty_history(), "invalid"
        if not isinstance(record, dict):
            return empty_history(), "invalid"
        runs = record.get("runs")
        success_ema = record.get("success_ema")
        failures = record.get("consecutive_failed_runs")
        last_seen = record.get("last_seen_epoch")
        if (
            not isinstance(runs, int)
            or runs < 1
            or not isinstance(success_ema, (int, float))
            or not 0 <= float(success_ema) <= 1
            or not isinstance(failures, int)
            or failures < 0
            or not isinstance(last_seen, int)
            or last_seen < 0
        ):
            return empty_history(), "invalid"
        clean[fingerprint] = {
            "runs": runs,
            "success_ema": round(float(success_ema), 6),
            "consecutive_failed_runs": failures,
            "last_seen_epoch": last_seen,
        }
    return {"version": _STATE_VERSION, "nodes": clean}, "loaded"


def browsing_runtime_names(candidate: dict[str, Any]) -> set[str]:
    providers = candidate.get("proxy-providers")
    if not isinstance(providers, dict):
        raise ValidationError("scheduler history requires candidate proxy-providers")
    names: set[str] = set()
    for provider_name, provider in providers.items():
        if not str(provider_name).startswith(_BROWSING_PROVIDER_PREFIX):
            continue
        payload = provider.get("payload") if isinstance(provider, dict) else None
        if not isinstance(payload, list):
            raise ValidationError("scheduler history found an invalid browsing provider")
        for proxy in payload:
            name = proxy.get("name") if isinstance(proxy, dict) else None
            if not isinstance(name, str) or not name:
                raise ValidationError("scheduler history found an unnamed browsing proxy")
            names.add(name)
    if not names:
        raise ValidationError("scheduler history found no browsing runtime names")
    return names


def preferred_stable_names(
    stable_names: set[str],
    history: dict[str, Any],
    fingerprint_key: bytes,
) -> set[str]:
    nodes = history.get("nodes", {})
    if not isinstance(nodes, dict):
        return set(stable_names)
    preferred: set[str] = set()
    for runtime_name in stable_names:
        record = nodes.get(fingerprint_runtime_name(runtime_name, fingerprint_key))
        if not isinstance(record, dict):
            preferred.add(runtime_name)
            continue
        runs = int(record.get("runs", 0))
        success_ema = float(record.get("success_ema", 1.0))
        failures = int(record.get("consecutive_failed_runs", 0))
        if runs < _MIN_HISTORY_RUNS or (
            success_ema >= _MIN_SUCCESS_EMA and failures == 0
        ):
            preferred.add(runtime_name)
    return preferred


def update_history(
    history: dict[str, Any],
    *,
    all_names: set[str],
    qualified_names: set[str],
    stable_names: set[str],
    fingerprint_key: bytes,
    now_epoch: int | None = None,
) -> dict[str, Any]:
    """Update only anonymous stability aggregates from the current run."""
    now = int(time.time()) if now_epoch is None else int(now_epoch)
    old_nodes = history.get("nodes", {})
    if not isinstance(old_nodes, dict):
        old_nodes = {}
    nodes: dict[str, dict[str, Any]] = {}

    for fingerprint, record in old_nodes.items():
        if not isinstance(record, dict):
            continue
        last_seen = record.get("last_seen_epoch")
        if isinstance(last_seen, int) and now - last_seen <= _MAX_AGE_SECONDS:
            nodes[str(fingerprint)] = dict(record)

    for runtime_name in sorted(all_names):
        fingerprint = fingerprint_runtime_name(runtime_name, fingerprint_key)
        old = nodes.get(fingerprint, {})
        old_runs = int(old.get("runs", 0))
        old_ema = float(old.get("success_ema", 1.0))
        old_failures = int(old.get("consecutive_failed_runs", 0))
        current_ratio = 1.0 if runtime_name in stable_names else (
            2.0 / 3.0 if runtime_name in qualified_names else 0.0
        )
        success_ema = current_ratio if old_runs == 0 else (
            old_ema * (1 - _ALPHA) + current_ratio * _ALPHA
        )
        nodes[fingerprint] = {
            "runs": old_runs + 1,
            "success_ema": round(success_ema, 6),
            "consecutive_failed_runs": 0 if runtime_name in qualified_names else old_failures + 1,
            "last_seen_epoch": now,
        }

    if len(nodes) > _MAX_RECORDS:
        newest = sorted(
            nodes.items(),
            key=lambda item: int(item[1].get("last_seen_epoch", 0)),
            reverse=True,
        )[:_MAX_RECORDS]
        nodes = dict(newest)
    return {"version": _STATE_VERSION, "nodes": dict(sorted(nodes.items()))}


def history_summary(
    *,
    load_status: str,
    before: dict[str, Any],
    after: dict[str, Any],
    stable_names: set[str],
    preferred_names: set[str],
) -> dict[str, Any]:
    before_nodes = before.get("nodes", {})
    after_nodes = after.get("nodes", {})
    return {
        "status": load_status,
        "records_before": len(before_nodes) if isinstance(before_nodes, dict) else 0,
        "records_after": len(after_nodes) if isinstance(after_nodes, dict) else 0,
        "stable_nodes": len(stable_names),
        "preferred_stable_nodes": len(preferred_names),
        "historically_demoted_nodes": len(stable_names - preferred_names),
    }


def _quote_re2_literal(value: str) -> str:
    return "".join(f"\\{character}" if character in _RE2_META else character for character in value)


def _exact_filter(names: set[str]) -> str:
    if not names:
        raise ValidationError("scheduler history cannot create an empty automatic filter")
    return "^(" + "|".join(_quote_re2_literal(name) for name in sorted(names)) + ")$"


def apply_history_preference(candidate_path: Path, preferred_names: set[str]) -> int:
    """Narrow existing stable browsing auto filters when history has enough preferred nodes."""
    if len(preferred_names) < _MIN_PREFERRED_AUTO_NODES:
        return 0
    try:
        original = candidate_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError("scheduler history could not read the qualified candidate") from exc
    config = load_yaml_file(candidate_path)
    if not isinstance(config, dict):
        raise ValidationError("scheduler history candidate must be a YAML mapping")
    groups = config.get("proxy-groups")
    if not isinstance(groups, list):
        raise ValidationError("scheduler history requires proxy groups")

    rewrites = 0
    for group in groups:
        if not isinstance(group, dict) or group.get("type") != "url-test":
            continue
        uses = group.get("use")
        if not isinstance(uses, list) or not uses:
            continue
        if not all(str(name).startswith(_BROWSING_PROVIDER_PREFIX) for name in uses):
            continue
        group["filter"] = _exact_filter(preferred_names)
        rewrites += 1
    if rewrites:
        header_lines: list[str] = []
        for line in original.splitlines():
            if not line.startswith("#"):
                break
            header_lines.append(line)
        header = "\n".join(header_lines) + ("\n" if header_lines else "")
        atomic_write(candidate_path, header + dump_yaml(config))
    return rewrites
