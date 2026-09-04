from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clash_relay.ai_application import run_ai_qualification
from clash_relay.errors import ClashRelayError


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Privately qualify generated AI nodes before production publication."
    )
    parser.add_argument("--candidate", type=_path, required=True)
    parser.add_argument("--policies", type=_path, required=True)
    parser.add_argument("--mihomo-bin", type=_path, required=True)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--cache", type=_path)
    parser.add_argument("--cache-key", type=_path)
    parser.add_argument("--next-cache", type=_path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_ai_qualification(
            candidate=args.candidate,
            policies=args.policies,
            mihomo_bin=args.mihomo_bin,
            workers=args.workers,
            cache=args.cache,
            cache_key=args.cache_key,
            next_cache=args.next_cache,
        )
    except ClashRelayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
