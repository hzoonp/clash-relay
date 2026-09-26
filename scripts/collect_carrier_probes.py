#!/usr/bin/env python3
"""Validate and merge three aggregate-only carrier producer files."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from clash_relay.carrier_collector import collect_carrier_probes
from clash_relay.carrier_qualification import parse_carrier_json_text
from clash_relay.errors import ClashRelayError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payloads = []
        for path in args.input:
            if path.stat().st_size > 16 * 1024:
                raise ValueError("aggregate too large")
            payloads.append(parse_carrier_json_text(path.read_text(encoding="utf-8")))
        merged = collect_carrier_probes(payloads)
        destination = args.output.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        return 0
    except (ClashRelayError, OSError, UnicodeError, ValueError):
        print("carrier collector failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
