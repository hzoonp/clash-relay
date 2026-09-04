from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from clash_relay.ai_qualification_cache import ai_cache_summary, parse_ai_cache_bytes
from clash_relay.config_loader import load_project
from clash_relay.errors import PublicationError
from clash_relay.publishers.cloudflare_kv import CloudflareKVPublisher


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Persist private AI qualification cache to KV.")
    parser.add_argument("--config", type=_path, default=Path("config.yaml"))
    parser.add_argument("--subscriptions", type=_path, default=Path("subscriptions.yaml"))
    parser.add_argument("--policies", type=_path, default=Path("policies.yaml"))
    parser.add_argument("--state", type=_path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        content = args.state.read_bytes()
    except OSError:
        print(json.dumps({"status": "skipped", "reason": "state_missing"}, sort_keys=True))
        return 0
    cache, parse_status = parse_ai_cache_bytes(content)
    if parse_status == "invalid":
        print(json.dumps({"status": "skipped", "reason": "state_invalid"}, sort_keys=True))
        return 0
    content = (
        json.dumps(cache, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")

    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    namespace_title = os.environ.get("CLOUDFLARE_KV_NAMESPACE_TITLE", "")
    if not token or not account_id or not namespace_title:
        print(json.dumps({"status": "skipped", "reason": "cloudflare_unavailable"}, sort_keys=True))
        return 0

    project = load_project(
        config_path=args.config,
        subscriptions_path=args.subscriptions,
        policies_path=args.policies,
    )
    production_key = str(project.config["publishing"]["cloudflare_kv"]["key"])
    try:
        result = CloudflareKVPublisher(
            token=token,
            account_id=account_id,
            namespace_title=namespace_title,
            key_name=f"{production_key}.ai-qualification-cache-v1",
        ).publish(content=content)
    except PublicationError:
        print(json.dumps({"status": "unavailable", "records_preserved": False}, sort_keys=True))
        return 0
    print(
        json.dumps(
            {
                "status": "published",
                "bytes": result["bytes"],
                "sha256": result["sha256"],
                **ai_cache_summary(cache),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
