from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clash_relay.browsing_application import run_browsing_qualification
from clash_relay.core_diagnostics import diagnose_browsing_core
from clash_relay.qualification_reliability import (
    QualificationFailureCategory,
    QualificationStageRejected,
)


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Privately qualify generated browsing nodes before production publication."
    )
    parser.add_argument("--candidate", type=_path, required=True)
    parser.add_argument("--policies", type=_path, required=True)
    parser.add_argument("--mihomo-bin", type=_path, required=True)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--required-successes", type=int, default=2)
    parser.add_argument("--history", type=_path)
    parser.add_argument("--history-key", type=_path)
    parser.add_argument("--next-history", type=_path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_browsing_qualification(
            candidate=args.candidate,
            policies=args.policies,
            mihomo_bin=args.mihomo_bin,
            workers=args.workers,
            attempts=args.attempts,
            required_successes=args.required_successes,
            history=args.history,
            history_key=args.history_key,
            next_history=args.next_history,
        )
    except QualificationStageRejected as exc:
        print(json.dumps(exc.as_result(), ensure_ascii=False, sort_keys=True))
        if exc.category is QualificationFailureCategory.CORE_REJECTION:
            try:
                diagnostic = diagnose_browsing_core(args.candidate, args.mihomo_bin)
            except Exception as diagnostic_error:  # diagnostic must never mask rejection
                diagnostic = {"status": "unavailable", "reason": type(diagnostic_error).__name__}
            print(json.dumps(diagnostic, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
