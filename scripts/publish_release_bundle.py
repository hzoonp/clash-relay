from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clash_relay.config_loader import load_project
from clash_relay.errors import ClashRelayError
from clash_relay.production_application import publish_production_release


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stage and activate a versioned private Cloudflare KV production release."
    )
    parser.add_argument("--config", type=_path, default=Path("config.yaml"))
    parser.add_argument("--subscriptions", type=_path, default=Path("subscriptions.yaml"))
    parser.add_argument("--policies", type=_path, default=Path("policies.yaml"))
    parser.add_argument("--candidate", type=_path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        project = load_project(
            config_path=args.config,
            subscriptions_path=args.subscriptions,
            policies_path=args.policies,
        )
        result = publish_production_release(project=project, candidate_path=args.candidate)
    except (OSError, ClashRelayError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
