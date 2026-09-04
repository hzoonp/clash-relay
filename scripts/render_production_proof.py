from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clash_relay.errors import ClashRelayError
from clash_relay.production_application import render_production_proof_application


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render a privacy-safe production proof.")
    parser.add_argument("--candidate", type=_path, required=True)
    parser.add_argument("--audit", type=_path, required=True)
    parser.add_argument("--browsing", type=_path, required=True)
    parser.add_argument("--ai", type=_path, required=True)
    parser.add_argument("--qualification", type=_path)
    parser.add_argument("--release", type=_path)
    parser.add_argument("--validated-core", action="append")
    parser.add_argument("--validated-cores-report", type=_path)
    parser.add_argument("--publication-status", choices=("dry-run", "published"), required=True)
    parser.add_argument("--markdown", type=_path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        proof = render_production_proof_application(
            candidate=args.candidate,
            audit=args.audit,
            browsing=args.browsing,
            ai=args.ai,
            qualification=args.qualification,
            release=args.release,
            validated_cores=tuple(args.validated_core or ()),
            validated_cores_report=args.validated_cores_report,
            publication_status=args.publication_status,
            markdown=args.markdown,
        )
    except ClashRelayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(proof, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
