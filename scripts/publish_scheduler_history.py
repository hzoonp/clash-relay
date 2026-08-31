from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from clash_relay.config_loader import load_project
from clash_relay.errors import PublicationError
from clash_relay.production_metrics import (
    append_metrics_run,
    build_metrics_run,
    metrics_summary,
    parse_metrics_bytes,
)
from clash_relay.publishers.cloudflare_kv import CloudflareKVPublisher
from clash_relay.scheduler_history import parse_history_bytes


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise ValueError("aggregate metrics source must be a JSON mapping")
    return document


def _persist_metrics(
    *,
    token: str,
    account_id: str,
    namespace_title: str,
    production_key: str,
    private_dir: Path,
) -> dict[str, Any]:
    paths = {
        "candidate": private_dir / "config.yaml",
        "browsing": private_dir / "browsing-qualification-summary.json",
        "ai": private_dir / "ai-qualification-summary.json",
    }
    if not all(path.is_file() for path in paths.values()):
        return {"status": "skipped", "reason": "aggregate_sources_missing"}
    publisher = CloudflareKVPublisher(
        token=token,
        account_id=account_id,
        namespace_title=namespace_title,
        key_name=f"{production_key}.production-metrics-v1",
    )
    try:
        existing = publisher.read()
        state, load_status = parse_metrics_bytes(existing)
        run = build_metrics_run(
            candidate_path=paths["candidate"],
            browsing=_load_json(paths["browsing"]),
            ai=_load_json(paths["ai"]),
        )
        next_state = append_metrics_run(state, run)
        content = (
            json.dumps(next_state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        published = publisher.publish(content=content)
    except (OSError, ValueError, json.JSONDecodeError, PublicationError):
        return {"status": "unavailable"}
    return {
        "status": "published",
        "load_status": load_status,
        "bytes": published["bytes"],
        **metrics_summary(next_state),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Persist private browsing scheduler history and aggregate production metrics to KV."
    )
    parser.add_argument("--config", type=_path, default=Path("config.yaml"))
    parser.add_argument("--subscriptions", type=_path, default=Path("subscriptions.yaml"))
    parser.add_argument("--services", type=_path, default=Path("services.yaml"))
    parser.add_argument("--policies", type=_path, default=Path("policies.yaml"))
    parser.add_argument("--state", type=_path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    namespace_title = os.environ.get("CLOUDFLARE_KV_NAMESPACE_TITLE", "")
    if not token or not account_id or not namespace_title:
        print(json.dumps({"status": "skipped", "reason": "cloudflare_unavailable"}, sort_keys=True))
        return 0

    project = load_project(
        config_path=args.config,
        subscriptions_path=args.subscriptions,
        services_path=args.services,
        policies_path=args.policies,
    )
    production_key = str(project.config["publishing"]["cloudflare_kv"]["key"])
    metrics = _persist_metrics(
        token=token,
        account_id=account_id,
        namespace_title=namespace_title,
        production_key=production_key,
        private_dir=args.state.parent,
    )

    try:
        content = args.state.read_bytes()
    except OSError:
        print(
            json.dumps(
                {"status": "skipped", "reason": "state_missing", "production_metrics": metrics},
                sort_keys=True,
            )
        )
        return 0
    history, parse_status = parse_history_bytes(content)
    if parse_status == "invalid":
        print(
            json.dumps(
                {"status": "skipped", "reason": "state_invalid", "production_metrics": metrics},
                sort_keys=True,
            )
        )
        return 0
    content = (
        json.dumps(history, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")

    state_key = f"{production_key}.scheduler-state-v2"
    try:
        result = CloudflareKVPublisher(
            token=token,
            account_id=account_id,
            namespace_title=namespace_title,
            key_name=state_key,
        ).publish(content=content)
    except PublicationError:
        print(
            json.dumps(
                {
                    "status": "unavailable",
                    "records_preserved": False,
                    "production_metrics": metrics,
                },
                sort_keys=True,
            )
        )
        return 0
    print(
        json.dumps(
            {
                "status": "published",
                "state_version": int(history.get("version", 0)),
                "bytes": result["bytes"],
                "sha256": result["sha256"],
                "records": len(history["nodes"]),
                "production_metrics": metrics,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
