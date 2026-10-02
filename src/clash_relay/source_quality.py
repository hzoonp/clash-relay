"""Privacy-safe aggregate source quality analytics for production runs."""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from .errors import ValidationError
from .qualification_observability import safe_qualification_observability
from .runtime_names import valid_source_id

_SAFE_PROTOCOLS = frozenset(
    {
        "ss",
        "ssr",
        "vmess",
        "vless",
        "trojan",
        "http",
        "socks5",
        "snell",
        "hysteria",
        "hysteria2",
        "tuic",
        "anytls",
        "wireguard",
        "ssh",
        "mieru",
        "masque",
        "unknown",
    }
)
_SAFE_REGIONS = frozenset({"hk", "tw", "sg", "jp", "us", "kr", "other"})
_SAFE_STATUSES = frozenset({"ok", "failed", "unknown"})
_SAFE_STAGES = frozenset(
    {"hostname", "endpoint", "browsing", "transport", "ai", "service_hardening", "final"}
)


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(min(1.0, max(0.0, numerator / denominator)), 4)


def _safe_latency(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return round(number, 3)


def _source_quality_row(row: dict[str, Any]) -> dict[str, Any]:
    source_id = row.get("id")
    if not valid_source_id(source_id):
        raise ValidationError("source quality contains an invalid source id")

    input_nodes = _count(row.get("input_nodes"))
    parsed_valid = _count(row.get("parsed_valid_nodes"))
    post_dedup = _count(row.get("post_dedup_nodes"))
    generated_runtime = _count(row.get("generated_runtime_entries"))
    final_runtime = _count(row.get("final_runtime_entries"))

    parse_rate = _rate(parsed_valid, input_nodes)
    admission_rate = _rate(post_dedup, parsed_valid)
    if generated_runtime > 0:
        runtime_survival = _rate(final_runtime, generated_runtime)
    elif post_dedup > 0:
        runtime_survival = 0.0
    else:
        runtime_survival = 1.0

    stability_score = (
        round(100.0 * min(parse_rate, admission_rate, runtime_survival), 1)
        if input_nodes > 0
        else 0.0
    )
    by_stage = row.get("by_stage")
    safe_stage = (
        {
            str(stage): _count(count)
            for stage, count in sorted(by_stage.items())
            if isinstance(stage, str) and stage in _SAFE_STAGES
        }
        if isinstance(by_stage, dict)
        else {}
    )
    status = row.get("status")
    safe_status = status if isinstance(status, str) and status in _SAFE_STATUSES else "unknown"
    removed_at_stage = row.get("removed_at_stage")
    if not isinstance(removed_at_stage, str) or removed_at_stage not in _SAFE_STAGES:
        removed_at_stage = None

    return {
        "id": source_id,
        "status": safe_status,
        "input_nodes": input_nodes,
        "parsed_valid_nodes": parsed_valid,
        "post_dedup_nodes": post_dedup,
        "generated_runtime_entries": generated_runtime,
        "final_runtime_entries": final_runtime,
        "skipped_invalid_nodes": _count(row.get("skipped_invalid_nodes")),
        "removed_runtime_entries": _count(row.get("removed_runtime_entries")),
        "parse_rate": parse_rate,
        "admission_rate": admission_rate,
        "runtime_survival_rate": runtime_survival,
        "stability_score": stability_score,
        "removed_at_stage": removed_at_stage,
        "removed_by_stage": safe_stage,
    }


def _final_runtime_dimensions(candidate: dict[str, Any]) -> tuple[dict[str, int], dict[str, int]]:
    protocols: dict[str, set[str]] = {}
    regions: dict[str, set[str]] = {}

    def record_proxy(proxy: Any, *, region: str | None = None) -> None:
        if not isinstance(proxy, dict):
            return
        name = proxy.get("name")
        proxy_type = proxy.get("type")
        if not isinstance(name, str) or not isinstance(proxy_type, str):
            return
        protocol = proxy_type if proxy_type in _SAFE_PROTOCOLS else "unknown"
        protocols.setdefault(protocol, set()).add(name)
        if region in _SAFE_REGIONS:
            regions.setdefault(str(region), set()).add(name)

    top_level = candidate.get("proxies")
    if isinstance(top_level, list):
        for proxy in top_level:
            record_proxy(proxy)

    providers = candidate.get("proxy-providers")
    if isinstance(providers, dict):
        for provider_name, provider in providers.items():
            region = None
            if isinstance(provider_name, str):
                suffix = provider_name.rsplit("_", 1)[-1].lower()
                if suffix in _SAFE_REGIONS:
                    region = suffix
            if not isinstance(provider, dict):
                continue
            payload = provider.get("payload")
            if not isinstance(payload, list):
                continue
            for proxy in payload:
                record_proxy(proxy, region=region)

    return (
        {name: len(values) for name, values in sorted(protocols.items())},
        {name: len(values) for name, values in sorted(regions.items())},
    )


def _qualification_dimensions(
    qualification: dict[str, Any],
    *,
    source_ids: list[str],
) -> tuple[dict[str, int], dict[str, int]]:
    safe = safe_qualification_observability(qualification, known_source_ids=source_ids)
    protocols: Counter[str] = Counter()
    regions: Counter[str] = Counter()
    for row in safe["removed_by_stage"].values():
        protocols.update(row["by_protocol"])
        regions.update(row["by_region"])
    return dict(sorted(protocols.items())), dict(sorted(regions.items()))


def _browsing_latency(browsing: dict[str, Any]) -> dict[str, float]:
    diagnostics = browsing.get("diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    raw_latency = diagnostics.get("qualified_latency_ms")
    raw_latency = raw_latency if isinstance(raw_latency, dict) else {}
    history = browsing.get("scheduler_history")
    history = history if isinstance(history, dict) else {}

    values = {
        "p50_ms": _safe_latency(raw_latency.get("p50")),
        "p95_ms": _safe_latency(raw_latency.get("p95")),
        "history_ema_ms": _safe_latency(history.get("cohort_latency_ema_ms")),
    }
    return {key: value for key, value in values.items() if value is not None}


def build_source_quality_report(
    *,
    source_stage_accounting: list[dict[str, Any]],
    qualification: dict[str, Any],
    candidate: dict[str, Any],
    browsing: dict[str, Any],
) -> dict[str, Any]:
    """Build aggregate-only source quality evidence with no node identities."""

    sources = [_source_quality_row(row) for row in source_stage_accounting]
    source_ids = [str(row["id"]) for row in sources]
    if len(source_ids) != len(set(source_ids)):
        raise ValidationError("source quality contains duplicate source ids")

    protocol_distribution, region_distribution = _final_runtime_dimensions(candidate)
    removed_protocols, removed_regions = _qualification_dimensions(
        qualification,
        source_ids=source_ids,
    )

    return {
        "schema_version": 1,
        "status": "ready",
        "score_definition": "100 * minimum(parse_rate, admission_rate, runtime_survival_rate)",
        "sources": sources,
        "aggregate": {
            "configured_sources": len(sources),
            "active_sources": sum(1 for row in sources if row["final_runtime_entries"] > 0),
            "fully_removed_sources": sum(
                1 for row in sources if row["removed_at_stage"] is not None
            ),
            "final_protocol_distribution": protocol_distribution,
            "final_region_distribution": region_distribution,
            "qualification_removed_by_protocol": removed_protocols,
            "qualification_removed_by_region": removed_regions,
            "browsing_latency": _browsing_latency(browsing),
        },
    }
