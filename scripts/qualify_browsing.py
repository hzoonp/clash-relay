from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clash_relay.browsing_qualification import (
    load_browsing_probe_spec,
    probe_browsing_nodes,
    rewrite_browsing_qualified_candidate,
)
from clash_relay.errors import ClashRelayError


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    diagnostics: dict[str, object] = {}
    try:
        probe = load_browsing_probe_spec(args.policies)
        qualified = probe_browsing_nodes(
            args.mihomo_bin,
            args.candidate,
            probe,
            workers=args.workers,
            attempts=args.attempts,
            required_successes=args.required_successes,
            diagnostics=diagnostics,
        )
        report = rewrite_browsing_qualified_candidate(args.candidate, qualified)
        print(
            json.dumps(
                {"status": "qualified", "diagnostics": diagnostics, **report},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except ClashRelayError as exc:
        if diagnostics:
            print(
                json.dumps(
                    {"status": "rejected", "diagnostics": diagnostics},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
