#!/usr/bin/env python3
"""Run the application-layer production candidate pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clash_relay.errors import ClashRelayError
from clash_relay.production_pipeline import (
    ProductionPipelineOutputs,
    ProjectPaths,
    QualificationPaths,
    run_production_pipeline,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--subscriptions", type=Path, default=Path("subscriptions.yaml"))
    parser.add_argument("--services", type=Path, default=Path("services.yaml"))
    parser.add_argument("--policies", type=Path, default=Path("policies.yaml"))
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--build-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mihomo-bin", type=Path, required=True)
    parser.add_argument("--stage-dir", type=Path, required=True)
    parser.add_argument("--browsing-report", type=Path, required=True)
    parser.add_argument("--ai-report", type=Path, required=True)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--history-key", type=Path)
    parser.add_argument("--next-history", type=Path)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--cache-key", type=Path)
    parser.add_argument("--next-cache", type=Path)
    parser.add_argument("--pre-audit", type=Path, required=True)
    parser.add_argument("--post-audit", type=Path, required=True)
    parser.add_argument("--qualification-report", type=Path, required=True)
    parser.add_argument("--summary-markdown", type=Path)
    parser.add_argument("--workers", type=int, default=12)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_production_pipeline(
            project_paths=ProjectPaths(
                config=args.config,
                subscriptions=args.subscriptions,
                services=args.services,
                policies=args.policies,
            ),
            qualification_paths=QualificationPaths(
                candidate=args.candidate,
                output=args.output,
                mihomo_bin=args.mihomo_bin,
                stage_dir=args.stage_dir,
                browsing_report=args.browsing_report,
                ai_report=args.ai_report,
                history=args.history,
                history_key=args.history_key,
                next_history=args.next_history,
                cache=args.cache,
                cache_key=args.cache_key,
                next_cache=args.next_cache,
            ),
            outputs=ProductionPipelineOutputs(
                pre_audit=args.pre_audit,
                post_audit=args.post_audit,
                qualification=args.qualification_report,
                summary_markdown=args.summary_markdown,
            ),
            build_report_path=args.build_report,
            workers=args.workers,
            script_dir=Path(__file__).resolve().parent,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except ClashRelayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
