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
from .validator import validate_generated_config

_STATE_VERSION = 3
_LEGACY_STATE_VERSIONS = frozenset({1, 2})
_DOMAIN = b"clash-relay/browsing-scheduler-history/v1"
_ALPHA = 0.30
_LATENCY_ALPHA = 0.25
_MIN_HISTORY_RUNS = 2
_MIN_SUCCESS_EMA = 0.80
_RECOVER_SUCCESS_EMA = 0.90
_DEMOTE_AFTER_FAILURES = 2
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
    return {
        "version": _STATE_VERSION,
        "nodes": {},
        "cohort": {"runs": 0, "latency_ema_ms": None, "last_seen_epoch": 0},
    }


def _clean_node_record(record: Any) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    runs = record.get("runs")
    success_ema = record.get("success_ema")
    failures = record.get("consecutive_failed_runs")
    last_seen = record.get("last_seen_epoch")
    historically_preferred = record.get("historically_preferred", True)
    if (
        not isinstance(runs, int)
        or isinstance(runs, bool)
        or runs < 1
        or not isinstance(success_ema, (int, float))
        or isinstance(success_ema, bool)
        or not 0 <= float(success_ema) <= 1
        or not isinstance(failures, int)
        or isinstance(failures, bool)
        or failures < 0
        or not isinstance(last_seen, int)
        or isinstance(last_seen, bool)
        or last_seen < 0
        or not isinstance(historically_preferred, bool)
    ):
        return None
    return {
        "runs": runs,
        "success_ema": round(float(success_ema), 6),
        "consecutive_failed_runs": failures,
        "last_seen_epoch": last_seen,
        "historically_preferred": historically_preferred,
    }


def _clean_cohort(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"runs": 0, "latency_ema_ms": None, "last_seen_epoch": 0}
    runs = value.get("runs", 0)
    latency = value.get("latency_ema_ms")
    last_seen = value.get("last_seen_epoch", 0)
    if not isinstance(runs, int) or isinstance(runs, bool) or runs < 0:
        runs = 0
    if latency is not None and (
        not isinstance(latency, (int, float))
        or isinstance(latency, bool)
        or float(latency) < 0
        or float(latency) > 120_000
    ):
        latency = None
    if not isinstance(last_seen, int) or isinstance(last_seen, bool) or last_seen < 0:
        last_seen = 0
    return {
        "runs": runs,
        "latency_ema_ms": None if latency is None else round(float(latency), 3),
        "last_seen_epoch": last_seen,
    }


def parse_history_bytes(content: bytes | None) -> tuple[dict[str, Any], str]:
    """Parse auxiliary state; v1/v2 migrate and malformed state safely degrades."""
    if not content:
        return empty_history(), "missing"
    try:
        document = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return empty_history(), "invalid"
    if not isinstance(document, dict):
        return empty_history(), "invalid"
    version = document.get("version")
    if version not in {*_LEGACY_STATE_VERSIONS, _STATE_VERSION}:
        return empty_history(), "invalid"
    nodes = document.get("nodes")
    if not isinstance(nodes, dict):
        return empty_history(), "invalid"
    clean: dict[str, dict[str, Any]] = {}
    for fingerprint, record in nodes.items():
        if not isinstance(fingerprint, str) or len(fingerprint) != 64:
            return empty_history(), "invalid"
        clean_record = _clean_node_record(record)
        if clean_record is None:
            return empty_history(), "invalid"
        clean[fingerprint] = clean_record
    legacy = version in _LEGACY_STATE_VERSIONS
    cohort = None if version == 1 else document.get("cohort")
    return {
        "version": _STATE_VERSION,
        "nodes": clean,
        "cohort": _clean_cohort(cohort),
    }, "migrated" if legacy else "loaded"


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


def _history_prefers(
    record: dict[str, Any],
    *,
    min_success_ema: float,
    recover_success_ema: float,
    demote_after_failures: int,
) -> bool:
    success_ema = float(record.get("success_ema", 1.0))
    failures = int(record.get("consecutive_failed_runs", 0))
    was_preferred = bool(record.get("historically_preferred", True))
    if was_preferred:
        if failures >= demote_after_failures:
            return False
        # Debounce a single transient failed run even though EMA reacts faster.
        if 0 < failures < demote_after_failures:
            return True
        return success_ema >= min_success_ema
    # Hysteresis: once demoted, recovery requires a stronger threshold and a
    # clean live-qualified run history.
    return failures == 0 and success_ema >= recover_success_ema


def preferred_stable_names(
    stable_names: set[str],
    history: dict[str, Any],
    fingerprint_key: bytes,
    *,
    now_epoch: int | None = None,
) -> set[str]:
    """Demote only mature, fresh, historically unstable nodes inside today's stable set."""
    nodes = history.get("nodes", {})
    if not isinstance(nodes, dict):
        return set(stable_names)
    now = int(time.time()) if now_epoch is None else int(now_epoch)
    preferred: set[str] = set()
    for runtime_name in stable_names:
        record = nodes.get(fingerprint_runtime_name(runtime_name, fingerprint_key))
        if not isinstance(record, dict):
            preferred.add(runtime_name)
            continue
        runs = int(record.get("runs", 0))
        last_seen = int(record.get("last_seen_epoch", 0))
        fresh = last_seen > 0 and now >= last_seen and now - last_seen <= _MAX_AGE_SECONDS
        if not fresh or runs < _MIN_HISTORY_RUNS:
            preferred.add(runtime_name)
            continue
        if _history_prefers(
            record,
            min_success_ema=_MIN_SUCCESS_EMA,
            recover_success_ema=_RECOVER_SUCCESS_EMA,
            demote_after_failures=_DEMOTE_AFTER_FAILURES,
        ):
            preferred.add(runtime_name)
    return preferred


def _update_cohort_latency(
    history: dict[str, Any], *, current_latency_ms: float | None, now_epoch: int
) -> dict[str, Any]:
    old = _clean_cohort(history.get("cohort"))
    if current_latency_ms is None:
        return old
    current = float(current_latency_ms)
    if current < 0 or current > 120_000:
        return old
    old_runs = int(old["runs"])
    old_latency = old["latency_ema_ms"]
    latency_ema = (
        current
        if old_runs == 0 or old_latency is None
        else float(old_latency) * (1 - _LATENCY_ALPHA) + current * _LATENCY_ALPHA
    )
    return {
        "runs": old_runs + 1,
        "latency_ema_ms": round(latency_ema, 3),
        "last_seen_epoch": now_epoch,
    }


def update_history(
    history: dict[str, Any],
    *,
    all_names: set[str],
    qualified_names: set[str],
    stable_names: set[str],
    fingerprint_key: bytes,
    preferred_names: set[str] | None = None,
    cohort_latency_ms: float | None = None,
    now_epoch: int | None = None,
) -> dict[str, Any]:
    """Update anonymous stability aggregates without allowing history to alter live admission."""
    now = int(time.time()) if now_epoch is None else int(now_epoch)
    old_nodes = history.get("nodes", {})
    if not isinstance(old_nodes, dict):
        old_nodes = {}
    nodes: dict[str, dict[str, Any]] = {}

    for fingerprint, record in old_nodes.items():
        if not isinstance(record, dict):
            continue
        last_seen = record.get("last_seen_epoch")
        if isinstance(last_seen, int) and not isinstance(last_seen, bool) and 0 <= now - last_seen <= _MAX_AGE_SECONDS:
            clean = _clean_node_record(record)
            if clean is not None:
                nodes[str(fingerprint)] = clean

    for runtime_name in sorted(all_names):
        fingerprint = fingerprint_runtime_name(runtime_name, fingerprint_key)
        old = nodes.get(fingerprint, {})
        old_runs = int(old.get("runs", 0))
        old_ema = float(old.get("success_ema", 1.0))
        old_failures = int(old.get("consecutive_failed_runs", 0))
        current_ratio = (
            1.0
            if runtime_name in stable_names
            else (2.0 / 3.0 if runtime_name in qualified_names else 0.0)
        )
        success_ema = (
            current_ratio if old_runs == 0 else old_ema * (1 - _ALPHA) + current_ratio * _ALPHA
        )
        historically_preferred = bool(old.get("historically_preferred", True))
        if preferred_names is not None and runtime_name in stable_names:
            historically_preferred = runtime_name in preferred_names
        nodes[fingerprint] = {
            "runs": old_runs + 1,
            "success_ema": round(success_ema, 6),
            "consecutive_failed_runs": 0 if runtime_name in qualified_names else old_failures + 1,
            "last_seen_epoch": now,
            "historically_preferred": historically_preferred,
        }

    if len(nodes) > _MAX_RECORDS:
        newest = sorted(
            nodes.items(),
            key=lambda item: int(item[1].get("last_seen_epoch", 0)),
            reverse=True,
        )[:_MAX_RECORDS]
        nodes = dict(newest)
    return {
        "version": _STATE_VERSION,
        "nodes": dict(sorted(nodes.items())),
        "cohort": _update_cohort_latency(
            history, current_latency_ms=cohort_latency_ms, now_epoch=now
        ),
    }


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
    cohort = _clean_cohort(after.get("cohort"))
    return {
        "status": load_status,
        "state_version": _STATE_VERSION,
        "records_before": len(before_nodes) if isinstance(before_nodes, dict) else 0,
        "records_after": len(after_nodes) if isinstance(after_nodes, dict) else 0,
        "stable_nodes": len(stable_names),
        "preferred_stable_nodes": len(preferred_names),
        "historically_demoted_nodes": len(stable_names - preferred_names),
        "cohort_latency_ema_ms": cohort["latency_ema_ms"],
        "cohort_runs": cohort["runs"],
    }


def _quote_re2_literal(value: str) -> str:
    return "".join(f"\\{character}" if character in _RE2_META else character for character in value)


def _exact_filter(names: set[str]) -> str:
    if not names:
        raise ValidationError("scheduler history cannot create an empty automatic filter")
    return "^(" + "|".join(_quote_re2_literal(name) for name in sorted(names)) + ")$"


def apply_history_preference(candidate_path: Path, preferred_names: set[str]) -> int:
    """Narrow stable browsing auto filters only where three preferred nodes remain."""
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
    providers = config.get("proxy-providers")
    if not isinstance(groups, list) or not isinstance(providers, dict):
        raise ValidationError("scheduler history requires proxy groups and providers")

    provider_names: dict[str, set[str]] = {}
    for provider_name, provider in providers.items():
        if not str(provider_name).startswith(_BROWSING_PROVIDER_PREFIX):
            continue
        payload = provider.get("payload") if isinstance(provider, dict) else None
        if not isinstance(payload, list):
            raise ValidationError("scheduler history found an invalid qualified browsing provider")
        provider_names[str(provider_name)] = {
            str(proxy["name"])
            for proxy in payload
            if isinstance(proxy, dict) and isinstance(proxy.get("name"), str)
        }

    rewrites = 0
    for group in groups:
        if not isinstance(group, dict) or group.get("type") != "url-test":
            continue
        uses = group.get("use")
        if not isinstance(uses, list) or not uses:
            continue
        if not all(str(name) in provider_names for name in uses):
            continue
        available = set().union(*(provider_names[str(name)] for name in uses))
        group_preferred = preferred_names & available
        if len(group_preferred) < _MIN_PREFERRED_AUTO_NODES:
            continue
        group["filter"] = _exact_filter(group_preferred)
        rewrites += 1
    if rewrites:
        validate_generated_config(config)
        header_lines: list[str] = []
        for line in original.splitlines():
            if not line.startswith("#"):
                break
            header_lines.append(line)
        header = "\n".join(header_lines) + ("\n" if header_lines else "")
        atomic_write(candidate_path, header + dump_yaml(config))
    return rewrites
