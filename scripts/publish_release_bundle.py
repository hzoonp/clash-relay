from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from clash_relay.config_loader import load_project
from clash_relay.errors import ClashRelayError, PublicationError
from clash_relay.mihomo import load_candidate
from clash_relay.publication import publication_gate
from clash_relay.publishers.cloudflare_kv import CloudflareKVPublisher
from clash_relay.release_bundle import publish_release_bundle
from clash_relay.validator import validate_generated_config


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stage and activate a versioned private Cloudflare KV production release."
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
        project = load_project(
            config_path=args.config,
            subscriptions_path=args.subscriptions,
            services_path=args.services,
            policies_path=args.policies,
        )
        publication_gate(project.config, "cloudflare_kv")
        candidate = load_candidate(args.candidate)
        validate_generated_config(candidate)
        try:
            content = args.candidate.read_bytes()
        except OSError as exc:
            raise PublicationError("failed to read production release candidate") from exc
        if not content:
            raise PublicationError("refusing to publish an empty production release candidate")

        token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        namespace_title = os.environ.get("CLOUDFLARE_KV_NAMESPACE_TITLE", "")
        if not token or not account_id or not namespace_title:
            raise PublicationError("Cloudflare credentials are required for production publication")
        production_key = str(project.config["publishing"]["cloudflare_kv"]["key"])

        def factory(key: str) -> CloudflareKVPublisher:
            return CloudflareKVPublisher(
                token=token,
                account_id=account_id,
                namespace_title=namespace_title,
                key_name=key,
            )

        result = publish_release_bundle(
            factory=factory,
            production_key=production_key,
            content=content,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ClashRelayError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
