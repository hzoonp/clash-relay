"""Persist observe-only carrier history in an independent aggregate KV key."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from typing import Any

from .carrier_history import (
    SCHEMA_VERSION,
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
    binding_passed: bool = True,
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
    observed = observe_campaign(history, report, binding_passed=binding_passed, now_epoch=now)
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
    lines = ["## Carrier observation history (advisory)", ""]
    lines.append(f"State: **{result.get('status', 'unavailable')}**")
    raw = result.get("history")
    if not isinstance(raw, Mapping):
        return "\n".join([*lines, ""])
    history = safe_history_summary(raw)
    lines.extend(
        [
            f"Recent campaigns: **{history['recent_campaign_count']}**  ",
            f"Consecutive valid campaigns: **{history['consecutive_valid_campaigns']}**",
            "",
            "| Carrier | Campaigns | Reachable ratio EMA | Median / p90 latency EMA (ms) | DNS / timeout / refused / connect failure / connected ratio EMA |",
            "| --- | ---: | ---: | ---: | ---: |",
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
            f"| {carrier} | {row['campaign_runs']} | {row['reachable_ratio_ema']} | "
            f"{row['median_latency_ms_ema']} / {row['p90_latency_ms_ema']} | {categories} |"
        )
    lines.append("")
    return "\n".join(lines)
