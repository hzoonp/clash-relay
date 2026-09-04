#!/usr/bin/env python3
"""Fetch the exact client-visible current production config for comparison."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clash_relay.config_loader import load_project
from clash_relay.errors import ClashRelayError
from clash_relay.production_application import fetch_current_production_config


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--subscriptions", type=Path, default=Path("subscriptions.yaml"))
    parser.add_argument("--policies", type=Path, default=Path("policies.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-missing", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        project = load_project(
            config_path=args.config,
            subscriptions_path=args.subscriptions,
            policies_path=args.policies,
        )
        result = fetch_current_production_config(
            project=project,
            output=args.output,
            allow_missing=args.allow_missing,
        )
    except ClashRelayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
