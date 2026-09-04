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


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise ValueError("aggregate metrics source must be a JSON mapping")
    return document


def _load_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return _load_json(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Persist aggregate-only bounded production metrics to Cloudflare KV."
    )
    parser.add_argument("--config", type=_path, default=Path("config.yaml"))
    parser.add_argument("--subscriptions", type=_path, default=Path("subscriptions.yaml"))
    parser.add_argument("--services", type=_path, default=Path("services.yaml"))
    parser.add_argument("--policies", type=_path, default=Path("policies.yaml"))
    parser.add_argument("--private-dir", type=_path, required=True)
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
    private_dir = args.private_dir
    required = {
        "candidate": private_dir / "config.yaml",
        "browsing": private_dir / "browsing-qualification-summary.json",
        "ai": private_dir / "ai-qualification-summary.json",
    }
    if not all(path.is_file() for path in required.values()):
        print(json.dumps({"status": "skipped", "reason": "aggregate_sources_missing"}, sort_keys=True))
        return 0

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
            candidate_path=required["candidate"],
            browsing=_load_json(required["browsing"]),
            ai=_load_json(required["ai"]),
            qualification=_load_optional_json(private_dir / "qualification-pipeline-summary.json"),
            release=_load_optional_json(private_dir / "release-publication.json"),
            mihomo_matrix=_load_optional_json(private_dir / "mihomo-validation-matrix.json"),
            promotion_guard=_load_optional_json(private_dir / "promotion-guard.json"),
            lifecycle=_load_optional_json(private_dir / "lifecycle-observability.json"),
        )
        next_state = append_metrics_run(state, run)
        content = (
            json.dumps(next_state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        published = publisher.publish(content=content)
    except (OSError, ValueError, json.JSONDecodeError, PublicationError):
        print(json.dumps({"status": "unavailable"}, sort_keys=True))
        return 0

    print(
        json.dumps(
            {
                "status": "published",
                "load_status": load_status,
                "bytes": published["bytes"],
                **metrics_summary(next_state),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
