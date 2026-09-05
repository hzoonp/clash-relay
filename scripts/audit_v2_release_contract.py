#!/usr/bin/env python3
"""Fail validation when the clean-slate v2 release contract regresses."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
STALE_PHASE_DOCS = (
    "docs/p13.md",
    "docs/p14-p18.md",
    "docs/p27-p32-architecture-consolidation.md",
    "docs/p33-p38-stabilization-v1.8.md",
    "docs/p39-p45-production-stabilization.md",
    "docs/migration-v1.8.md",
)


def _mapping(path: str) -> dict[str, object]:
    data = yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"v2 release audit: {path} is not a mapping")
    return data


def audit(root: Path = ROOT) -> list[str]:
    errors: list[str] = []

    with (root / "pyproject.toml").open("rb") as handle:
        version = tomllib.load(handle)["project"]["version"]
    if version != "2.0.0":
        errors.append(f"package version is {version!r}, expected '2.0.0'")
    notes = root / "docs" / "releases" / f"{version}.md"
    if not notes.is_file():
        errors.append(f"missing versioned release notes: {notes.relative_to(root)}")

    for relative in ("config.yaml", "subscriptions.yaml", "policies.yaml"):
        document = yaml.safe_load((root / relative).read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("version") != 2:
            errors.append(f"{relative} is not v2-only")

    subscriptions = yaml.safe_load((root / "subscriptions.yaml").read_text(encoding="utf-8"))
    entries = subscriptions.get("subscriptions", []) if isinstance(subscriptions, dict) else []
    first = next(
        (entry for entry in entries if isinstance(entry, dict) and entry.get("id") == "subscription_1"),
        None,
    )
    if not isinstance(first, dict):
        errors.append("subscription_1 declaration is missing")
    else:
        if first.get("allowed_uses") != ["browsing", "ai"]:
            errors.append("subscription_1 is not browsing/AI-only")
        if first.get("max_node_multiplier") != 2.0:
            errors.append("subscription_1 max_node_multiplier is not exactly 2.0")
        if "ingest_order" not in first:
            errors.append("subscription_1 lacks ingest_order")
        if "priority" in first:
            errors.append("removed subscription priority field returned")

    release_bundle = (root / "src/clash_relay/release_bundle.py").read_text(encoding="utf-8")
    for forbidden in ("legacy_previous", "legacy-previous-v1", 'f"{production_key}.previous-v1"'):
        if forbidden in release_bundle:
            errors.append(f"legacy rollback compatibility returned: {forbidden}")

    for relative in STALE_PHASE_DOCS:
        if (root / relative).exists():
            errors.append(f"stale phase document returned: {relative}")

    for relative in ("README.md", "README.zh-CN.md"):
        text = (root / relative).read_text(encoding="utf-8")
        for token in ("P26", "P18.1-P23", "v1.6.2"):
            if token in text:
                errors.append(f"{relative} contains phase/version-era implementation narration: {token}")

    workflow = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    for required in (
        "needs.validate.outputs.validated_sha == github.sha",
        'ref: ${{ needs.validate.outputs.validated_sha }}',
        'docs/releases/${VERSION}.md',
        "gh release create",
    ):
        if required not in workflow:
            errors.append(f"release workflow is missing v2 contract token: {required}")

    return errors


def main() -> int:
    errors = audit()
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 2
    print("v2 release contract audit: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
