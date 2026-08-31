#!/usr/bin/env python3
"""Render aggregate Routing V2 cutover shadow evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from clash_relay.config_loader import load_project
from clash_relay.routing_shadow import routing_shadow_summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--subscriptions", type=Path, default=Path("subscriptions.yaml"))
    parser.add_argument("--services", type=Path, default=Path("services.yaml"))
    parser.add_argument("--policies", type=Path, default=Path("policies.yaml"))
    return parser


def main() -> int:
    args = _parser().parse_args()
    project = load_project(
        config_path=args.config,
        subscriptions_path=args.subscriptions,
        services_path=args.services,
        policies_path=args.policies,
    )
    print(json.dumps(routing_shadow_summary(project), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
