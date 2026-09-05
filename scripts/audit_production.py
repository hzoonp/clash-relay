#!/usr/bin/env python3
"""Audit production source-use isolation and emit only aggregate statistics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from clash_relay.config_loader import load_project
from clash_relay.mihomo import load_candidate
from clash_relay.production_audit import render_production_summary_markdown
from clash_relay.production_pipeline import audit_candidate
from clash_relay.util import atomic_write


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--subscriptions", type=Path, default=Path("subscriptions.yaml"))
    parser.add_argument("--policies", type=Path, default=Path("policies.yaml"))
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--markdown", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    project = load_project(
        config_path=args.config,
        subscriptions_path=args.subscriptions,
        policies_path=args.policies,
    )
    candidate = load_candidate(args.candidate)
    build_report = None
    if args.report is not None:
        build_report = json.loads(args.report.read_text(encoding="utf-8"))
    summary = audit_candidate(project, candidate, build_report=build_report)
    if args.markdown is not None:
        atomic_write(args.markdown, render_production_summary_markdown(summary))
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
