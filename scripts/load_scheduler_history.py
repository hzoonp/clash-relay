from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from clash_relay.config_loader import load_project
from clash_relay.errors import PublicationError
from clash_relay.publishers.cloudflare_kv import CloudflareKVPublisher
from clash_relay.scheduler_history import derive_fingerprint_key, parse_history_bytes


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load private browsing scheduler history from KV.")
    parser.add_argument("--config", type=_path, default=Path("config.yaml"))
    parser.add_argument("--subscriptions", type=_path, default=Path("subscriptions.yaml"))
    parser.add_argument("--policies", type=_path, default=Path("policies.yaml"))
    parser.add_argument("--output", type=_path, required=True)
    parser.add_argument("--fingerprint-key-output", type=_path, required=True)
    return parser


def _read_state(
    *,
    token: str,
    account_id: str,
    namespace_title: str,
    production_key: str,
) -> tuple[bytes | None, str, str]:
    for suffix, source in (
        ("scheduler-state-v3", "v3"),
        ("scheduler-state-v2", "v2"),
        ("scheduler-state-v1", "v1"),
    ):
        try:
            content = CloudflareKVPublisher(
                token=token,
                account_id=account_id,
                namespace_title=namespace_title,
                key_name=f"{production_key}.{suffix}",
            ).read()
        except PublicationError:
            return None, "unavailable", "none"
        if content is not None:
            return content, "loaded", source
    return None, "missing", "none"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project = load_project(
        config_path=args.config,
        subscriptions_path=args.subscriptions,
        policies_path=args.policies,
    )
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    namespace_title = os.environ.get("CLOUDFLARE_KV_NAMESPACE_TITLE", "")
    production_key = str(project.config["publishing"]["cloudflare_kv"]["key"])

    content: bytes | None = None
    transport_status = "unavailable"
    source = "none"
    if token and account_id and namespace_title:
        content, transport_status, source = _read_state(
            token=token,
            account_id=account_id,
            namespace_title=namespace_title,
            production_key=production_key,
        )

    history, parse_status = parse_history_bytes(content)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(history, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    args.fingerprint_key_output.parent.mkdir(parents=True, exist_ok=True)
    if token and transport_status in {"loaded", "missing"}:
        args.fingerprint_key_output.write_text(
            derive_fingerprint_key(token).hex() + "\n", encoding="ascii"
        )
    else:
        args.fingerprint_key_output.write_text("", encoding="ascii")
    os.chmod(args.fingerprint_key_output, 0o600)

    nodes = history.get("nodes", {})
    print(
        json.dumps(
            {
                "status": transport_status,
                "source": source,
                "parse_status": parse_status,
                "state_version": int(history.get("version", 0)),
                "records": len(nodes) if isinstance(nodes, dict) else 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
