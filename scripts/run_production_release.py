#!/usr/bin/env python3
"""Run the one canonical production lifecycle used by GitHub Actions and operators."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from clash_relay.errors import ClashRelayError
from clash_relay.production_lifecycle import (
    ProductionLifecyclePaths,
    ProductionPipeline,
    resolve_publication_mode,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the canonical clash-relay production lifecycle."
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--publish", action="store_true", dest="publish")
    mode.add_argument("--dry-run", action="store_false", dest="publish")
    parser.set_defaults(publish=None)
    parser.add_argument("--event-name")
    parser.add_argument("--manual-publish")
    parser.add_argument("--workers", type=int, default=12)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        publish = resolve_publication_mode(
            explicit_publish=args.publish,
            event_name=args.event_name or os.environ.get("GITHUB_EVENT_NAME"),
            manual_publish=(
                args.manual_publish
                if args.manual_publish is not None
                else os.environ.get("CLASH_RELAY_MANUAL_PUBLISH")
            ),
        )
        result = ProductionPipeline(
            ProductionLifecyclePaths.canonical(args.root),
            publish=publish,
            workers=args.workers,
        ).run()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ClashRelayError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
