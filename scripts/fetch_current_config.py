#!/usr/bin/env python3
"""Fetch the exact client-visible current production config for comparison."""

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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--subscriptions", type=Path, default=Path("subscriptions.yaml"))
    parser.add_argument("--services", type=Path, default=Path("services.yaml"))
    parser.add_argument("--policies", type=Path, default=Path("policies.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-missing", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        namespace_title = os.environ.get("CLOUDFLARE_KV_NAMESPACE_TITLE", "")
        if not token or not account_id or not namespace_title:
            raise PublicationError("Cloudflare credentials are required for production baseline")
        project = load_project(
            config_path=args.config,
            subscriptions_path=args.subscriptions,
            services_path=args.services,
            policies_path=args.policies,
        )
        production_key = str(project.config["publishing"]["cloudflare_kv"]["key"])
        publisher = CloudflareKVPublisher(
            token=token,
            account_id=account_id,
            namespace_title=namespace_title,
            key_name=production_key,
        )
        current = publisher.read()
        if current is None:
            if not args.allow_missing:
                raise PublicationError("current production release is missing")
            args.output.unlink(missing_ok=True)
            print(json.dumps({"status": "absent"}, sort_keys=True))
            return 0
        if not current:
            raise PublicationError("current production release is empty")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(current)
        os.chmod(args.output, 0o600)
        print(
            json.dumps(
                {
                    "status": "fetched",
                    "bytes": len(current),
                    "sha256": hashlib.sha256(current).hexdigest(),
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
