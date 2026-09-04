from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clash_relay.errors import ClashRelayError
from clash_relay.mihomo_matrix_application import validate_mihomo_matrix


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate one private candidate against every pinned Mihomo core in a channel."
    )
    parser.add_argument("--candidate", type=_path, required=True)
    parser.add_argument("--manifest", type=_path, default=Path("tools/mihomo-versions.json"))
    parser.add_argument("--channel", default="stable")
    parser.add_argument("--work-dir", type=_path, required=True)
    parser.add_argument("--reuse-primary-bin", type=_path)
    parser.add_argument("--startup-seconds", type=float, default=1.5)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = validate_mihomo_matrix(
            candidate=args.candidate,
            manifest=args.manifest,
            channel=args.channel,
            work_dir=args.work_dir,
            reuse_primary_bin=args.reuse_primary_bin,
            startup_seconds=args.startup_seconds,
        )
    except ClashRelayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
