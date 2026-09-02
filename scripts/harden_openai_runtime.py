#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from clash_relay.ai_runtime_reliability import rewrite_openai_client_path_candidate
from clash_relay.errors import ClashRelayError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Harden the qualified OpenAI route with client-local health checks."
    )
    parser.add_argument("--candidate", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        report = rewrite_openai_client_path_candidate(args.candidate)
    except ClashRelayError as exc:
        print(f"error: {exc}")
        return 2
    print(json.dumps({"status": "passed", **report}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
