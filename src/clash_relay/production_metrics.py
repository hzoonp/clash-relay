"""Privacy-safe longitudinal production metrics."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

_STATE_VERSION = 1
_MAX_RUNS = 30


def empty_metrics() -> dict[str, Any]:
    return {"version": _STATE_VERSION, "runs": []}


def parse_metrics_bytes(content: bytes | None) -> tuple[dict[str, Any], str]:
    if not content:
        return empty_metrics(), "missing"
    try:
        document = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return empty_metrics(), "invalid"
    if not isinstance(document, dict) or document.get("version") != _STATE_VERSION:
        return empty_metrics(), "invalid"
    runs = document.get("runs")
    if not isinstance(runs, list):
        return empty_metrics(), "invalid"
    clean = [run for run in runs if _valid_run(run)]
    if len(clean) != len(runs):
        return empty_metrics(), "invalid"
    return {"version": _STATE_VERSION, "runs": clean[-_MAX_RUNS:]}, "loaded"


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _valid_run(run: Any) -> bool:
    if not isinstance(run, dict):
        return False
    required = ("epoch", "candidate_sha256", "candidate_bytes", "browsing", "ai")
    if any(key not in run for key in required):
        return False
    if not isinstance(run["epoch"], int) or run["epoch"] < 0:
        return False
    sha = run["candidate_sha256"]
    if not isinstance(sha, str) or len(sha) != 64:
        return False
    if not isinstance(run["candidate_bytes"], int) or run["candidate_bytes"] < 0:
        return False
    return isinstance(run["browsing"], dict) and isinstance(run["ai"], dict)


def build_metrics_run(
    *,
    candidate_path: Path,
    browsing: dict[str, Any],
    ai: dict[str, Any],
    epoch: int | None = None,
) -> dict[str, Any]:
    content = candidate_path.read_bytes()
    browsing_diagnostics = browsing.get("diagnostics", {})
    if not isinstance(browsing_diagnostics, dict):
        browsing_diagnostics = {}
    latency = browsing_diagnostics.get("qualified_latency_ms", {})
    if not isinstance(latency, dict):
        latency = {}
    history = browsing.get("scheduler_history", {})
    if not isinstance(history, dict):
        history = {}

    ai_diagnostics = ai.get("diagnostics", {})
    if not isinstance(ai_diagnostics, dict):
        ai_diagnostics = {}
    probes = ai_diagnostics.get("probes", {})
    if not isinstance(probes, dict):
        probes = {}
    qualification_cache = ai.get("qualification_cache", {})
    if not isinstance(qualification_cache, dict):
        qualification_cache = {}

    qualified_by_service = {
        str(name): int(summary.get("qualified_nodes", 0))
        for name, summary in probes.items()
        if isinstance(summary, dict) and isinstance(summary.get("qualified_nodes", 0), int)
    }
    return {
        "epoch": int(time.time()) if epoch is None else int(epoch),
        "candidate_sha256": hashlib.sha256(content).hexdigest(),
        "candidate_bytes": len(content),
        "browsing": {
            "tested": int(browsing_diagnostics.get("tested_nodes", 0)),
            "qualified": int(browsing_diagnostics.get("qualified_nodes", 0)),
            "stable": int(browsing.get("stable_nodes", 0)),
            "reserve": int(browsing.get("reserve_nodes", 0)),
            "rejected": int(browsing_diagnostics.get("failed_nodes", 0)),
            "p50_ms": _number(latency.get("p50")),
            "p95_ms": _number(latency.get("p95")),
            "historically_demoted": int(history.get("historically_demoted_nodes", 0)),
            "history_latency_ema_ms": _number(history.get("cohort_latency_ema_ms")),
        },
        "ai": {
            "candidate_nodes": int(ai_diagnostics.get("tested_nodes", 0)),
            "qualified_by_service": dict(sorted(qualified_by_service.items())),
            "live_service_probes": int(qualification_cache.get("live_service_probes", 0)),
            "cache_pass_hits": int(qualification_cache.get("cache_pass_hits", 0)),
            "cache_fail_hits": int(qualification_cache.get("cache_fail_hits", 0)),
        },
    }


def append_metrics_run(state: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    if not _valid_run(run):
        raise ValueError("invalid aggregate production metrics run")
    existing = state.get("runs", [])
    if not isinstance(existing, list):
        existing = []
    runs = [item for item in existing if _valid_run(item)]
    if not runs or runs[-1].get("candidate_sha256") != run["candidate_sha256"]:
        runs.append(run)
    else:
        runs[-1] = run
    return {"version": _STATE_VERSION, "runs": runs[-_MAX_RUNS:]}


def metrics_summary(state: dict[str, Any]) -> dict[str, Any]:
    runs = state.get("runs", [])
    if not isinstance(runs, list) or not runs:
        return {"runs": 0}
    latest = runs[-1]
    previous = runs[-2] if len(runs) > 1 else None
    summary: dict[str, Any] = {
        "runs": len(runs),
        "latest_candidate_sha256": latest["candidate_sha256"],
        "latest_candidate_bytes": latest["candidate_bytes"],
        "latest_browsing_qualified": latest["browsing"].get("qualified", 0),
        "latest_ai_live_service_probes": latest["ai"].get("live_service_probes", 0),
    }
    if isinstance(previous, dict):
        summary["previous_candidate_sha256"] = previous.get("candidate_sha256")
    return summary
