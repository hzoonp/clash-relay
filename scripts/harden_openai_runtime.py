#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from clash_relay.errors import ClashRelayError
from clash_relay.openai_application import harden_openai_client_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Harden the qualified OpenAI route with client-local health checks."
    )
    parser.add_argument("--candidate", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = harden_openai_client_path(args.candidate)
    except ClashRelayError as exc:
        print(f"error: {exc}")
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
