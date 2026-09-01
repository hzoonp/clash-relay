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
        description="Preserve the currently published config before replacing it."
    )
    parser.add_argument("--config", type=_path, default=Path("config.yaml"))
    parser.add_argument("--subscriptions", type=_path, default=Path("subscriptions.yaml"))
    parser.add_argument("--services", type=_path, default=Path("services.yaml"))
    parser.add_argument("--policies", type=_path, default=Path("policies.yaml"))
    parser.add_argument("--candidate", type=_path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        candidate = args.candidate.read_bytes()
        if not candidate:
            raise PublicationError("refusing to snapshot around an empty production candidate")
        token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        namespace_title = os.environ.get("CLOUDFLARE_KV_NAMESPACE_TITLE", "")
        if not token or not account_id or not namespace_title:
            raise PublicationError("Cloudflare credentials are required for recovery snapshotting")

        project = load_project(
            config_path=args.config,
            subscriptions_path=args.subscriptions,
            services_path=args.services,
            policies_path=args.policies,
        )
        production_key = str(project.config["publishing"]["cloudflare_kv"]["key"])
        current = CloudflareKVPublisher(
            token=token,
            account_id=account_id,
            namespace_title=namespace_title,
            key_name=production_key,
        ).read()
        if current is None:
            print(json.dumps({"status": "no-current"}, sort_keys=True))
            return 0
        current_sha = hashlib.sha256(current).hexdigest()
        candidate_sha = hashlib.sha256(candidate).hexdigest()
        if current_sha == candidate_sha:
            print(
                json.dumps(
                    {"status": "unchanged", "bytes": len(current), "sha256": current_sha},
                    sort_keys=True,
                )
            )
            return 0

        previous_key = f"{production_key}.previous-v1"
        result = CloudflareKVPublisher(
            token=token,
            account_id=account_id,
            namespace_title=namespace_title,
            key_name=previous_key,
        ).publish(content=current)
        print(
            json.dumps(
                {
                    "status": "snapshotted",
                    "bytes": result["bytes"],
                    "sha256": result["sha256"],
                },
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ClashRelayError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
