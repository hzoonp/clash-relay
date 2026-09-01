"""Privacy-safe longitudinal production metrics."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

_STATE_VERSION = 1
_MAX_RUNS = 30
_RELEASE_ID = re.compile(r"^[0-9a-f]{64}$")
_MAX_TIMING_MS = 24 * 60 * 60 * 1000


def empty_metrics() -> dict[str, Any]:
    return {"version": _STATE_VERSION, "runs": []}


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _non_negative_int(value: Any, default: int = 0) -> int:
    return (
        int(value)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else default
    )


def _safe_sha(value: Any) -> str | None:
    return value if isinstance(value, str) and _RELEASE_ID.fullmatch(value) else None


def _clean_browsing(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    clean: dict[str, Any] = {
        "tested": _non_negative_int(value.get("tested")),
        "qualified": _non_negative_int(value.get("qualified")),
        "stable": _non_negative_int(value.get("stable")),
        "reserve": _non_negative_int(value.get("reserve")),
        "rejected": _non_negative_int(value.get("rejected")),
        "p50_ms": _number(value.get("p50_ms")),
        "p95_ms": _number(value.get("p95_ms")),
        "historically_demoted": _non_negative_int(value.get("historically_demoted")),
        "history_latency_ema_ms": _number(value.get("history_latency_ema_ms")),
    }
    regions = value.get("regions")
    if isinstance(regions, dict):
        safe_regions: dict[str, dict[str, int]] = {}
        for region, summary in sorted(regions.items()):
            if (
                not isinstance(region, str)
                or not region
                or len(region) > 32
                or not isinstance(summary, dict)
            ):
                continue
            safe_regions[region] = {
                key: _non_negative_int(summary.get(key))
                for key in (
                    "tested",
                    "qualified",
                    "stable",
                    "preferred_stable",
                    "historically_demoted",
                )
            }
        if safe_regions:
            clean["regions"] = safe_regions
    return clean


def _clean_ai(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    qualified = value.get("qualified_by_service")
    safe_services: dict[str, int] = {}
    if isinstance(qualified, dict):
        for name, count in sorted(qualified.items()):
            if isinstance(name, str) and 0 < len(name) <= 64:
                safe_services[name] = _non_negative_int(count)
    return {
        "candidate_nodes": _non_negative_int(value.get("candidate_nodes")),
        "qualified_by_service": safe_services,
        "live_service_probes": _non_negative_int(value.get("live_service_probes")),
        "cache_pass_hits": _non_negative_int(value.get("cache_pass_hits")),
        "cache_fail_hits": _non_negative_int(value.get("cache_fail_hits")),
    }


def _clean_release(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    if status not in {"published", "unchanged"}:
        return None
    release_id = _safe_sha(value.get("release_id"))
    if release_id is None:
        return None
    clean: dict[str, Any] = {
        "status": status,
        "release_id": release_id,
        "production_changed": value.get("production_changed") is True,
    }
    previous = _safe_sha(value.get("previous_release_id"))
    if previous is not None:
        clean["previous_release_id"] = previous
    if value.get("first_release") is True:
        clean["first_release"] = True
    return clean


def _clean_mihomo(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    tags = value.get("validated_cores")
    if not isinstance(tags, list) or not all(
        isinstance(tag, str) and 0 < len(tag) <= 64 for tag in tags
    ):
        return None
    return {
        "status": "passed" if value.get("status") == "passed" else "unknown",
        "validated_core_count": len(tags),
        "validated_cores": list(dict.fromkeys(tags)),
    }


def _clean_performance(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    clean: dict[str, float] = {}
    for name, duration in sorted(value.items()):
        if not isinstance(name, str) or not name or len(name) > 64:
            continue
        number = _number(duration)
        if number is None or float(number) < 0 or float(number) > _MAX_TIMING_MS:
            continue
        clean[name] = round(float(number), 3)
    return clean or None


def _clean_qualification(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    if status != "qualified":
        return None
    stages = value.get("stages")
    stage_count = len(stages) if isinstance(stages, list) else 0
    return {"status": "qualified", "stage_count": stage_count}


def _clean_run(run: Any) -> dict[str, Any] | None:
    if not isinstance(run, dict):
        return None
    epoch = run.get("epoch")
    sha = _safe_sha(run.get("candidate_sha256"))
    candidate_bytes = run.get("candidate_bytes")
    browsing = _clean_browsing(run.get("browsing"))
    ai = _clean_ai(run.get("ai"))
    if (
        not isinstance(epoch, int)
        or isinstance(epoch, bool)
        or epoch < 0
        or sha is None
        or not isinstance(candidate_bytes, int)
        or isinstance(candidate_bytes, bool)
        or candidate_bytes < 0
        or browsing is None
        or ai is None
    ):
        return None
    clean: dict[str, Any] = {
        "epoch": epoch,
        "candidate_sha256": sha,
        "candidate_bytes": candidate_bytes,
        "browsing": browsing,
        "ai": ai,
    }
    for key, cleaner in (
        ("release", _clean_release),
        ("mihomo", _clean_mihomo),
        ("performance", _clean_performance),
        ("qualification", _clean_qualification),
    ):
        value = cleaner(run.get(key))
        if value is not None:
            clean[key] = value
    return clean


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
    clean: list[dict[str, Any]] = []
    for run in runs:
        item = _clean_run(run)
        if item is None:
            return empty_metrics(), "invalid"
        clean.append(item)
    return {"version": _STATE_VERSION, "runs": clean[-_MAX_RUNS:]}, "loaded"


def build_metrics_run(
    *,
    candidate_path: Path,
    browsing: dict[str, Any],
    ai: dict[str, Any],
    qualification: dict[str, Any] | None = None,
    release: dict[str, Any] | None = None,
    mihomo_matrix: dict[str, Any] | None = None,
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
    run: dict[str, Any] = {
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
            "regions": history.get("regions", {}),
        },
        "ai": {
            "candidate_nodes": int(ai_diagnostics.get("tested_nodes", 0)),
            "qualified_by_service": dict(sorted(qualified_by_service.items())),
            "live_service_probes": int(qualification_cache.get("live_service_probes", 0)),
            "cache_pass_hits": int(qualification_cache.get("cache_pass_hits", 0)),
            "cache_fail_hits": int(qualification_cache.get("cache_fail_hits", 0)),
        },
    }
    if qualification is not None:
        run["qualification"] = qualification
        timings = qualification.get("timings_ms") if isinstance(qualification, dict) else None
        if timings is not None:
            run["performance"] = timings
    if release is not None:
        run["release"] = release
    if mihomo_matrix is not None:
        run["mihomo"] = mihomo_matrix
    clean = _clean_run(run)
    if clean is None:
        raise ValueError("failed to build aggregate production metrics run")
    return clean


def append_metrics_run(state: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    clean_run = _clean_run(run)
    if clean_run is None:
        raise ValueError("invalid aggregate production metrics run")
    existing = state.get("runs", [])
    runs: list[dict[str, Any]] = []
    if isinstance(existing, list):
        for item in existing:
            clean = _clean_run(item)
            if clean is not None:
                runs.append(clean)
    if not runs or runs[-1].get("candidate_sha256") != clean_run["candidate_sha256"]:
        runs.append(clean_run)
    else:
        runs[-1] = clean_run
    return {"version": _STATE_VERSION, "runs": runs[-_MAX_RUNS:]}


def metrics_summary(state: dict[str, Any]) -> dict[str, Any]:
    runs = state.get("runs", [])
    if not isinstance(runs, list) or not runs:
        return {"runs": 0}
    latest = runs[-1]
    previous = runs[-2] if len(runs) > 1 else None
    release = latest.get("release", {}) if isinstance(latest.get("release"), dict) else {}
    mihomo = latest.get("mihomo", {}) if isinstance(latest.get("mihomo"), dict) else {}
    performance = (
        latest.get("performance", {}) if isinstance(latest.get("performance"), dict) else {}
    )
    summary: dict[str, Any] = {
        "runs": len(runs),
        "latest_candidate_sha256": latest["candidate_sha256"],
        "latest_candidate_bytes": latest["candidate_bytes"],
        "latest_browsing_qualified": latest["browsing"].get("qualified", 0),
        "latest_ai_live_service_probes": latest["ai"].get("live_service_probes", 0),
        "latest_release_status": release.get("status", "unknown"),
        "latest_validated_core_count": mihomo.get("validated_core_count", 0),
        "latest_pipeline_total_ms": performance.get("total", 0.0),
    }
    if isinstance(previous, dict):
        summary["previous_candidate_sha256"] = previous.get("candidate_sha256")
    return summary
