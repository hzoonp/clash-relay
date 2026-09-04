#!/usr/bin/env python3
"""Download a pinned official Mihomo release asset and verify its GitHub digest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clash_relay.errors import ClashRelayError
from clash_relay.mihomo_download import download_pinned_mihomo


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("tools/mihomo-versions.json"))
    parser.add_argument("--channel", choices=["stable", "prerelease"], default="stable")
    parser.add_argument("--tag")
    parser.add_argument("--arch", default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--allow-missing-digest",
        action="store_true",
        help="Not recommended. Permit an asset when GitHub returns no sha256 digest.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = download_pinned_mihomo(
            manifest=args.manifest,
            channel=args.channel,
            tag=args.tag,
            arch=args.arch,
            output=args.output,
            allow_missing_digest=args.allow_missing_digest,
        )
    except (OSError, ValueError, ClashRelayError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
