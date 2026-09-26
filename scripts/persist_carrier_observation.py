#!/usr/bin/env python3
"""Phase-B commit entrypoint for carrier observation history.

Consumes ONLY an observation receipt issued by a fully successful canonical
preflight (``.work/carrier-observation-receipt.json``). A bare carrier JSON
payload is rejected: every digest, the collected epoch, and the validated SHA
are re-verified here before any Cloudflare KV write. Digest, epoch, or SHA
mismatches fail closed without touching history.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from clash_relay.carrier_observation_receipt import (
    RECEIPT_FILENAME,
    commit_carrier_observation_receipt,
)
from clash_relay.carrier_qualification import parse_carrier_json_text
from clash_relay.config_loader import load_project
from clash_relay.errors import ClashRelayError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--receipt",
        type=Path,
        default=None,
        help=f"observation receipt path (default: <root>/.work/{RECEIPT_FILENAME})",
    )
    parser.add_argument(
        "--expect-sha",
        default=None,
        help="validated SHA the receipt must carry (default: GITHUB_SHA)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = args.root.resolve()
        receipt_path = root / ".work" / RECEIPT_FILENAME if args.receipt is None else args.receipt
        if receipt_path.stat().st_size > 64 * 1024:
            raise ValueError("carrier observation receipt exceeds size limit")
        receipt = parse_carrier_json_text(receipt_path.read_text(encoding="utf-8"))
        project = load_project(
            config_path=root / "config.yaml",
            subscriptions_path=root / "subscriptions.yaml",
            policies_path=root / "policies.yaml",
        )
        result = commit_carrier_observation_receipt(
            project=project,
            receipt=receipt,
            env=os.environ,
            expect_validated_sha=args.expect_sha,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (ClashRelayError, OSError, UnicodeError, ValueError):
        # Never echo receipt contents, credentials, or aggregate material.
        print("carrier observation commit failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
