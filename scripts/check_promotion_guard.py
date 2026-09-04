#!/usr/bin/env python3
"""Compare a qualified candidate with current production before activation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clash_relay.config_loader import load_project
from clash_relay.errors import ClashRelayError
from clash_relay.production_application import run_promotion_guard


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--subscriptions", type=Path, default=Path("subscriptions.yaml"))
    parser.add_argument("--policies", type=Path, default=Path("policies.yaml"))
    parser.add_argument("--guard", type=Path, default=Path("promotion-guard.yaml"))
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--markdown", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        project = load_project(
            config_path=args.config,
            subscriptions_path=args.subscriptions,
            policies_path=args.policies,
        )
        report = run_promotion_guard(
            project=project,
            candidate_path=args.candidate,
            baseline_path=args.baseline,
            guard_path=args.guard,
            report_path=args.report,
            markdown_path=args.markdown,
        )
    except ClashRelayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
