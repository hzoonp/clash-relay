#!/usr/bin/env python3
"""Migrate one monolithic Policy Model v1 document to the v2 physical manifest."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from clash_relay.errors import ClashRelayError, ConfigurationError
from clash_relay.policy_contract import load_policy_contract
from clash_relay.policy_document import load_policy_document
from clash_relay.schema import validate_schema
from clash_relay.util import dump_yaml, load_yaml_file

_LAYOUT = {
    "routing": ("routing",),
    "scheduling": ("scheduler", "probes"),
    "classification": ("capabilities", "cost_levels", "country_classification"),
    "topology": ("pools", "chains"),
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Migrate Policy Model v1 to v2 fragments.")
    parser.add_argument("--input", type=Path, default=Path("policies.yaml"))
    parser.add_argument("--output", type=Path, default=Path("policies.yaml"))
    parser.add_argument("--fragment-dir", type=Path, default=Path("policies"))
    parser.add_argument("--force", action="store_true")
    return parser


def migrate(*, source: Path, output: Path, fragment_dir: Path, force: bool) -> None:
    raw = load_yaml_file(source)
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ConfigurationError("policy migration requires a monolithic version: 1 document")
    validate_schema(raw, "policies.schema.json", source=str(source))
    load_policy_contract(raw)

    output_parent = output.resolve().parent
    fragment_dir = fragment_dir.resolve()
    try:
        fragment_dir.relative_to(output_parent)
    except ValueError as exc:
        raise ConfigurationError(
            "policy fragment directory must stay under the output root"
        ) from exc

    if fragment_dir.exists():
        if not force:
            raise ConfigurationError("policy fragment directory already exists; pass --force")
        shutil.rmtree(fragment_dir)
    fragment_dir.mkdir(parents=True)

    fragments: dict[str, str] = {}
    for name, keys in _LAYOUT.items():
        document = {key: raw[key] for key in keys if key in raw}
        target = fragment_dir / f"{name}.yaml"
        target.write_text(dump_yaml(document), encoding="utf-8")
        fragments[name] = target.relative_to(output_parent).as_posix()

    manifest = {"version": 2, "fragments": fragments}
    output.write_text(dump_yaml(manifest), encoding="utf-8")
    normalized = load_policy_document(output)
    if normalized.document != raw:
        raise ConfigurationError("policy v2 migration changed the normalized policy document")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        migrate(
            source=args.input,
            output=args.output,
            fragment_dir=args.fragment_dir,
            force=args.force,
        )
        print("policy model v2 migration: passed")
        return 0
    except (OSError, ClashRelayError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
