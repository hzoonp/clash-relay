"""Persist observe-only carrier history in an independent aggregate KV key.

The only sanctioned way to reach this module's write path is through a
validated carrier-observation receipt (see ``carrier_observation_receipt``).
Campaigns that never passed candidate binding have no representation here.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from typing import Any

from .carrier_history import (
    SCHEMA_VERSION,
    WINDOW_DAYS,
    observe_campaign,
    parse_history_bytes,
    safe_history_summary,
    serialize_history,
)
from .carrier_qualification import safe_carrier_report
from .config_loader import ProjectDefinition
from .errors import PublicationError
from .publishers.cloudflare_kv import CloudflareKVPublisher


def _publisher(project: ProjectDefinition, env: Mapping[str, str]) -> CloudflareKVPublisher | None:
    token = env.get("CLOUDFLARE_API_TOKEN", "").strip()
    account = env.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    namespace = env.get("CLOUDFLARE_KV_NAMESPACE_TITLE", "").strip()
    if not token or not account or not namespace:
        return None
    production_key = str(project.config["publishing"]["cloudflare_kv"]["key"])
    return CloudflareKVPublisher(
        token=token,
        account_id=account,
        namespace_title=namespace,
        key_name=f"{production_key}.carrier-observation-history-v1",
    )


def persist_carrier_observation(
    *,
    project: ProjectDefinition,
    report: Mapping[str, Any],
    env: Mapping[str, str] | None = None,
    now_epoch: int | None = None,
) -> dict[str, Any]:
    """Record one already-bound campaign without exposing probe identities."""
    safe_carrier_report(report)
    environment = os.environ if env is None else env
    publisher = _publisher(project, environment)
    if publisher is None:
        return {"status": "skipped", "reason": "cloudflare_unavailable"}
    now = int(time.time()) if now_epoch is None else now_epoch
    try:
        previous = publisher.read()
    except PublicationError:
        return {"status": "unavailable", "reason": "history_read_failed"}
    history = parse_history_bytes(previous, now_epoch=now)
    observed = observe_campaign(history, report, now_epoch=now)
    summary = safe_history_summary(observed)
    if observed == history and previous is not None:
        return {"status": "unchanged", "state_version": SCHEMA_VERSION, "history": summary}
    content = serialize_history(observed)
    try:
        receipt = publisher.publish(content=content)
    except PublicationError:
        return {"status": "unavailable", "reason": "history_publish_failed"}
    return {
        "status": "published",
        "state_version": SCHEMA_VERSION,
        "bytes": receipt["bytes"],
        "sha256": receipt["sha256"],
        "history": summary,
    }


def render_carrier_history_markdown(result: Mapping[str, Any]) -> str:
    """Actions summary with numeric aggregates only; no trend verdict."""
    lines = [
        "## Carrier observation history (advisory)",
        "",
        f"State: **{result.get('status', 'unavailable')}**",
    ]
    raw = result.get("history")
    if not isinstance(raw, Mapping):
        return "\n".join([*lines, ""])
    history = safe_history_summary(raw)
    lines.extend(
        [
            f"Recent campaigns (trailing {history['window_days']}-day window): "
            f"**{history['recent_campaign_count']}**  ",
            f"Consecutive valid campaigns: **{history['consecutive_valid_campaigns']}**  ",
            "Lifetime counters below are saturating caps, not rolling window values.",
            "",
            "| Carrier | Campaign runs (lifetime) | Reachable ratio EMA | Latency samples (lifetime) | Median / p90 latency EMA (ms) | DNS / timeout / refused / connect failure / connected ratio EMA |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for carrier, row in history["carriers"].items():
        outcomes = row["outcome_ratio_ema"]
        categories = " / ".join(
            str(outcomes[name])
            for name in (
                "dns_failure",
                "connect_timeout",
                "connection_refused",
                "connect_failure",
                "tcp_connected",
            )
        )
        lines.append(
            f"| {carrier} | {row['campaign_runs_lifetime']} | {row['reachable_ratio_ema']} | "
            f"{row['latency_sample_runs']} | {row['median_latency_ms_ema']} / {row['p90_latency_ms_ema']} | "
            f"{categories} |"
        )
    lines.extend(
        [
            "",
            f"Latency EMAs only reflect campaigns with reachable samples "
            f"(``latency_sample_runs``); a zero-reachable campaign never reads as latency 0. "
            f"History window: **{WINDOW_DAYS} days**.",
            "",
        ]
    )
    return "\n".join(lines)
