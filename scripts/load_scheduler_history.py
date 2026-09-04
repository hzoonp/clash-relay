from __future__ import annotations

import argparse
import json
from pathlib import Path

from clash_relay.config_loader import load_project
from clash_relay.production_application import load_scheduler_history_state


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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project = load_project(
        config_path=args.config,
        subscriptions_path=args.subscriptions,
        policies_path=args.policies,
    )
    result = load_scheduler_history_state(
        project=project,
        output=args.output,
        fingerprint_key_output=args.fingerprint_key_output,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
