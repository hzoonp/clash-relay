"""Application service for bounded privacy-safe operational SLO state."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from .config_loader import ProjectDefinition
from .errors import PublicationError
from .operational_slo import append_slo_attempt, parse_slo_bytes, slo_summary
from .publishers.cloudflare_kv import CloudflareKVPublisher


def persist_operational_slo(
    *,
    project: ProjectDefinition,
    attempt: dict[str, Any],
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Append one aggregate production outcome without affecting release validity."""

    values = os.environ if env is None else env
    token = values.get("CLOUDFLARE_API_TOKEN", "")
    account_id = values.get("CLOUDFLARE_ACCOUNT_ID", "")
    namespace_title = values.get("CLOUDFLARE_KV_NAMESPACE_TITLE", "")
    if not token or not account_id or not namespace_title:
        return {"status": "skipped", "reason": "cloudflare_unavailable"}

    production_key = str(project.config["publishing"]["cloudflare_kv"]["key"])
    publisher = CloudflareKVPublisher(
        token=token,
        account_id=account_id,
        namespace_title=namespace_title,
        key_name=f"{production_key}.operational-slo-v1",
    )
    try:
        existing = publisher.read()
        state, load_status = parse_slo_bytes(existing)
        next_state = append_slo_attempt(state, attempt)
        content = (
            json.dumps(next_state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        published = publisher.publish(content=content)
    except (ValueError, PublicationError):
        return {"status": "unavailable"}
    return {
        "status": "published",
        "load_status": load_status,
        "bytes": published["bytes"],
        **slo_summary(next_state),
    }
