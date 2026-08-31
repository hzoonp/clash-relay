from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from clash_relay.config_loader import load_project
from clash_relay.errors import ClashRelayError, PublicationError
from clash_relay.publishers.cloudflare_kv import CloudflareKVPublisher


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch the private previous production config for explicit rollback."
    )
    parser.add_argument("--config", type=_path, default=Path("config.yaml"))
    parser.add_argument("--subscriptions", type=_path, default=Path("subscriptions.yaml"))
    parser.add_argument("--services", type=_path, default=Path("services.yaml"))
    parser.add_argument("--policies", type=_path, default=Path("policies.yaml"))
    parser.add_argument("--output", type=_path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        namespace_title = os.environ.get("CLOUDFLARE_KV_NAMESPACE_TITLE", "")
        if not token or not account_id or not namespace_title:
            raise PublicationError("Cloudflare credentials are required for rollback")
        project = load_project(
            config_path=args.config,
            subscriptions_path=args.subscriptions,
            services_path=args.services,
            policies_path=args.policies,
        )
        production_key = str(project.config["publishing"]["cloudflare_kv"]["key"])
        previous = CloudflareKVPublisher(
            token=token,
            account_id=account_id,
            namespace_title=namespace_title,
            key_name=f"{production_key}.previous-v1",
        ).read()
        if previous is None:
            raise PublicationError("no previous validated production config is available")
        if not previous:
            raise PublicationError("previous production config is empty")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(previous)
        os.chmod(args.output, 0o600)
        print(
            json.dumps(
                {
                    "status": "fetched",
                    "bytes": len(previous),
                    "sha256": hashlib.sha256(previous).hexdigest(),
                },
                sort_keys=True,
            )
        )
        return 0
    except ClashRelayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
