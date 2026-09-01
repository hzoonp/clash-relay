from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clash_relay.errors import ClashRelayError
from clash_relay.qualification_pipeline import run_qualification_pipeline


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run browsing, transport, and AI qualification as one staged pipeline."
    )
    parser.add_argument("--candidate", type=_path, required=True)
    parser.add_argument("--output", type=_path, required=True)
    parser.add_argument("--policies", type=_path, required=True)
    parser.add_argument("--mihomo-bin", type=_path, required=True)
    parser.add_argument("--stage-dir", type=_path, required=True)
    parser.add_argument("--browsing-report", type=_path, required=True)
    parser.add_argument("--ai-report", type=_path, required=True)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--history", type=_path)
    parser.add_argument("--history-key", type=_path)
    parser.add_argument("--next-history", type=_path)
    parser.add_argument("--cache", type=_path)
    parser.add_argument("--cache-key", type=_path)
    parser.add_argument("--next-cache", type=_path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_qualification_pipeline(
            candidate=args.candidate,
            output=args.output,
            policies=args.policies,
            mihomo_bin=args.mihomo_bin,
            stage_dir=args.stage_dir,
            browsing_report=args.browsing_report,
            ai_report=args.ai_report,
            workers=args.workers,
            history=args.history,
            history_key=args.history_key,
            next_history=args.next_history,
            cache=args.cache,
            cache_key=args.cache_key,
            next_cache=args.next_cache,
            script_dir=Path(__file__).resolve().parent,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except ClashRelayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
