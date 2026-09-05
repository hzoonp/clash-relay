#!/usr/bin/env python3
"""Fail validation when the clean-slate v2 release contract regresses."""

from __future__ import annotations

import re
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
_RUNTIME_COMPATIBILITY_TOKENS = (
    "--allow-legacy-openai-client-path",
    "allow_legacy_openai_client_path",
    "allow_legacy_server_qualified",
    "_legacy_server_qualified_shape",
    "legacy_server_qualified",
    "historical_exact_bytes",
    "legacy_previous",
    "legacy-previous-v1",
)
_PHASE_TOKEN = re.compile(r"\bP\d+(?:\.\d+)?(?:-P?\d+(?:\.\d+)?)?\b")


def _iter_runtime_text_files(root: Path):
    for relative_dir, suffixes in (
        ("src", (".py",)),
        ("scripts", (".py",)),
        (".github/workflows", (".yml", ".yaml")),
    ):
        base = root / relative_dir
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in suffixes:
                continue
            if path == root / "scripts/audit_v2_release_contract.py":
                continue
            yield path


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
        (
            entry
            for entry in entries
            if isinstance(entry, dict) and entry.get("id") == "subscription_1"
        ),
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
    if 'f"{production_key}.previous-v1"' in release_bundle:
        errors.append("legacy previous-v1 rollback key returned")

    for path in _iter_runtime_text_files(root):
        text = path.read_text(encoding="utf-8")
        for token in _RUNTIME_COMPATIBILITY_TOKENS:
            if token in text:
                errors.append(
                    f"runtime compatibility token {token!r} returned in {path.relative_to(root)}"
                )

    for relative in STALE_PHASE_DOCS:
        if (root / relative).exists():
            errors.append(f"stale phase document returned: {relative}")

    for path in sorted((root / "tests").glob("test_p[0-9]*.py")):
        errors.append(f"phase-era test filename returned: {path.relative_to(root)}")

    docs_root = root / "docs"
    for path in sorted(docs_root.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        match = _PHASE_TOKEN.search(text)
        if match is not None:
            errors.append(
                f"durable documentation contains phase-era token {match.group(0)!r}: {path.relative_to(root)}"
            )

    for relative in ("README.md", "README.zh-CN.md"):
        text = (root / relative).read_text(encoding="utf-8")
        match = _PHASE_TOKEN.search(text)
        if match is not None:
            errors.append(f"{relative} contains phase-era token {match.group(0)!r}")

    workflow = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    for required in (
        "needs.validate.outputs.validated_sha == github.sha",
        "ref: ${{ needs.validate.outputs.validated_sha }}",
        "docs/releases/${VERSION}.md",
        "gh release create",
    ):
        if required not in workflow:
            errors.append(f"release workflow is missing v2 contract token: {required}")

    rollback = (root / ".github/workflows/rollback.yml").read_text(encoding="utf-8")
    if "fetch_previous_config.py" not in rollback:
        errors.append("rollback workflow no longer resolves the versioned previous release")
    if "audit_production.py" not in rollback or "validate_mihomo_matrix.py" not in rollback:
        errors.append("rollback workflow bypasses current policy or stable Mihomo validation")

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
