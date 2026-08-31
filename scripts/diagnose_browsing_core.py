from __future__ import annotations

import argparse
import json
from pathlib import Path

from clash_relay.core_diagnostics import diagnose_browsing_core


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Emit a redacted structural diagnostic for browsing qualification config failures."
    )
    parser.add_argument("--candidate", type=_path, required=True)
    parser.add_argument("--mihomo-bin", type=_path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = diagnose_browsing_core(args.candidate, args.mihomo_bin)
    except Exception as exc:  # diagnostic path must never mask the original failure
        payload = {"status": "unavailable", "reason": type(exc).__name__}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
