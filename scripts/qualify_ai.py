from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clash_relay.ai_qualification import (
    load_ai_probe_specs,
    probe_ai_nodes,
    rewrite_ai_qualified_candidate,
)
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        probes = load_ai_probe_specs(args.policies)
        qualified = probe_ai_nodes(args.mihomo_bin, args.candidate, probes)
        report = rewrite_ai_qualified_candidate(args.candidate, qualified)
        print(json.dumps({"status": "qualified", **report}, ensure_ascii=False, sort_keys=True))
        return 0
    except ClashRelayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
