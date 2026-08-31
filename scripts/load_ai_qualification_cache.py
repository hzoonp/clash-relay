from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from clash_relay.ai_qualification_cache import derive_ai_cache_key, parse_ai_cache_bytes
from clash_relay.config_loader import load_project
from clash_relay.errors import PublicationError
from clash_relay.publishers.cloudflare_kv import CloudflareKVPublisher


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load private AI qualification cache from KV.")
    parser.add_argument("--config", type=_path, default=Path("config.yaml"))
    parser.add_argument("--subscriptions", type=_path, default=Path("subscriptions.yaml"))
    parser.add_argument("--services", type=_path, default=Path("services.yaml"))
    parser.add_argument("--policies", type=_path, default=Path("policies.yaml"))
    parser.add_argument("--output", type=_path, required=True)
    parser.add_argument("--fingerprint-key-output", type=_path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project = load_project(
        config_path=args.config,
        subscriptions_path=args.subscriptions,
        services_path=args.services,
        policies_path=args.policies,
    )
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    namespace_title = os.environ.get("CLOUDFLARE_KV_NAMESPACE_TITLE", "")
    production_key = str(project.config["publishing"]["cloudflare_kv"]["key"])
    cache_key = f"{production_key}.ai-qualification-cache-v1"

    content: bytes | None = None
    transport_status = "unavailable"
    if token and account_id and namespace_title:
        try:
            content = CloudflareKVPublisher(
                token=token,
                account_id=account_id,
                namespace_title=namespace_title,
                key_name=cache_key,
            ).read()
            transport_status = "loaded" if content is not None else "missing"
        except PublicationError:
            transport_status = "unavailable"

    cache, parse_status = parse_ai_cache_bytes(content)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(cache, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    args.fingerprint_key_output.parent.mkdir(parents=True, exist_ok=True)
    if token and transport_status in {"loaded", "missing"}:
        args.fingerprint_key_output.write_text(
            derive_ai_cache_key(token).hex() + "\n", encoding="ascii"
        )
    else:
        args.fingerprint_key_output.write_text("", encoding="ascii")
    os.chmod(args.fingerprint_key_output, 0o600)

    nodes = cache.get("nodes", {})
    print(
        json.dumps(
            {
                "status": transport_status,
                "parse_status": parse_status,
                "records": len(nodes) if isinstance(nodes, dict) else 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
