"""Publish aggregate-only scheduler evidence derived from private production metrics."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from .config_loader import ProjectDefinition
from .production_metrics import parse_metrics_bytes
from .publishers.cloudflare_kv import CloudflareKVPublisher
from .errors import PublicationError
from .scheduler_evidence import compile_scheduler_evidence


def _environment(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _production_key(project: ProjectDefinition) -> str:
    return str(project.config["publishing"]["cloudflare_kv"]["key"])


def publish_scheduler_observation(
    *,
    project: ProjectDefinition,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Publish one reviewable evidence snapshot without node identity or URLs."""

    environment = _environment(env)
    token = environment.get("CLOUDFLARE_API_TOKEN", "").strip()
    account_id = environment.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    namespace_title = environment.get("CLOUDFLARE_KV_NAMESPACE_TITLE", "").strip()
    if not token or not account_id or not namespace_title:
        return {"status": "skipped", "reason": "cloudflare_unavailable"}

    production_key = _production_key(project)
    try:
        metrics = CloudflareKVPublisher(
            token=token,
            account_id=account_id,
            namespace_title=namespace_title,
            key_name=f"{production_key}.production-metrics-v1",
        ).read()
    except PublicationError:
        return {"status": "unavailable", "reason": "metrics_read_failed"}

    state, load_status = parse_metrics_bytes(metrics)
    if load_status != "loaded":
        return {"status": "skipped", "reason": f"metrics_{load_status}"}

    evidence = compile_scheduler_evidence(state)
    content = (
        json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    try:
        published = CloudflareKVPublisher(
            token=token,
            account_id=account_id,
            namespace_title=namespace_title,
            key_name=f"{production_key}.scheduler-evidence-v1",
        ).publish(content=content)
    except PublicationError:
        return {"status": "unavailable", "reason": "evidence_publish_failed"}

    return {
        "status": "published",
        "load_status": load_status,
        "mode": evidence["mode"],
        "privacy": evidence["privacy"],
        "evidence_status": evidence["status"],
        "sample_runs": evidence["sample_runs"],
        "bytes": published["bytes"],
        "sha256": published["sha256"],
    }
