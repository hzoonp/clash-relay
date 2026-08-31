from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clash_relay.errors import ClashRelayError, ValidationError
from clash_relay.production_proof import build_production_proof, render_production_proof_markdown


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _load_json(path: Path, label: str) -> dict:
    try:
        with path.open(encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"failed to load {label} for production proof") from exc
    if not isinstance(document, dict):
        raise ValidationError(f"{label} for production proof must be a mapping")
    return document


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render a privacy-safe production proof.")
    parser.add_argument("--candidate", type=_path, required=True)
    parser.add_argument("--audit", type=_path, required=True)
    parser.add_argument("--browsing", type=_path, required=True)
    parser.add_argument("--ai", type=_path, required=True)
    parser.add_argument("--validated-core", action="append", required=True)
    parser.add_argument("--publication-status", choices=("dry-run", "published"), required=True)
    parser.add_argument("--markdown", type=_path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        proof = build_production_proof(
            candidate_path=args.candidate,
            audit=_load_json(args.audit, "post-qualification audit"),
            browsing=_load_json(args.browsing, "browsing qualification"),
            ai=_load_json(args.ai, "AI qualification"),
            validated_cores=tuple(args.validated_core),
            publication_status=args.publication_status,
        )
        markdown = render_production_proof_markdown(proof)
        if args.markdown is not None:
            args.markdown.parent.mkdir(parents=True, exist_ok=True)
            args.markdown.write_text(markdown, encoding="utf-8")
        print(json.dumps(proof, ensure_ascii=False, sort_keys=True))
        return 0
    except ClashRelayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
