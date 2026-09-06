"""Best-effort persistence for aggregate production failure trends."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .config_loader import load_project
from .errors import ClashRelayError
from .production_metrics import append_failure_metric, metrics_summary, parse_metrics_bytes
from .publishers.cloudflare_kv import CloudflareKVPublisher


def _environment(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def persist_failure_diagnostic(
    *,
    root: Path,
    diagnostic: dict[str, Any],
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Append one sanitized failed attempt without exposing exception material."""

    values = _environment(env)
    config = root / "config.yaml"
    subscriptions = root / "subscriptions.yaml"
    policies = root / "policies.yaml"
    if not config.is_file() or not subscriptions.is_file() or not policies.is_file():
        return {"status": "skipped", "reason": "canonical_declarations_missing"}

    token = values.get("CLOUDFLARE_API_TOKEN", "")
    account_id = values.get("CLOUDFLARE_ACCOUNT_ID", "")
    namespace_title = values.get("CLOUDFLARE_KV_NAMESPACE_TITLE", "")
    if not token or not account_id or not namespace_title:
        return {"status": "skipped", "reason": "cloudflare_unavailable"}

    try:
        project = load_project(
            config_path=config,
            subscriptions_path=subscriptions,
            policies_path=policies,
        )
        production_key = str(project.config["publishing"]["cloudflare_kv"]["key"])
        publisher = CloudflareKVPublisher(
            token=token,
            account_id=account_id,
            namespace_title=namespace_title,
            key_name=f"{production_key}.production-metrics-v1",
        )
        existing = publisher.read()
        state, load_status = parse_metrics_bytes(existing)
        next_state = append_failure_metric(state, diagnostic)
        content = (
            json.dumps(next_state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        published = publisher.publish(content=content)
    except (OSError, ValueError, ClashRelayError):
        return {"status": "unavailable"}

    summary = metrics_summary(next_state)
    return {
        "status": "published",
        "load_status": load_status,
        "bytes": published["bytes"],
        "failure_runs": summary["failure_runs"],
        "failure_categories": summary["failure_categories"],
        "latest_failure_category": summary["latest_failure_category"],
        "recent_failure_rate": summary["recent_failure_rate"],
        "recent_failure_streak": summary["recent_failure_streak"],
    }
