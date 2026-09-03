#!/usr/bin/env python3
"""Render the aggregate-only P37 release manifest from private stage reports."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from clash_relay.errors import ClashRelayError, ValidationError
from clash_relay.mihomo import load_candidate
from clash_relay.policy_document import load_policy_document
from clash_relay.release_manifest import build_release_manifest, render_release_manifest_markdown
from clash_relay.util import atomic_write


def _json(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"could not read release-manifest input {path.name!r}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"release-manifest input {path.name!r} must be an object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--promotion-guard", type=Path)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--release", type=Path)
    parser.add_argument("--policies", type=Path, default=Path("policies.yaml"))
    parser.add_argument("--publication-status", choices=("dry-run", "published"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        audit = _json(args.audit)
        qualification = _json(args.qualification)
        matrix = _json(args.matrix)
        if audit is None or qualification is None or matrix is None:
            raise ValidationError("release manifest requires audit, qualification, and matrix")
        manifest = build_release_manifest(
            candidate=load_candidate(args.candidate),
            candidate_bytes=args.candidate.read_bytes(),
            audit=audit,
            qualification=qualification,
            promotion_guard=_json(args.promotion_guard),
            matrix=matrix,
            release=_json(args.release),
            publication_status=args.publication_status,
            policy_model_version=load_policy_document(args.policies).model_version,
            commit_sha=os.environ.get("GITHUB_SHA") or None,
        )
        atomic_write(
            args.output,
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        if args.markdown is not None:
            atomic_write(args.markdown, render_release_manifest_markdown(manifest))
        print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ClashRelayError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
