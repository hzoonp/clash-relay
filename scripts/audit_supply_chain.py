#!/usr/bin/env python3
"""Fail closed when P50 validation or dependency supply-chain gates regress."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHA_REF = re.compile(r"^[0-9a-f]{40}$")


def _logical_requirements(path: Path) -> list[str]:
    logical: list[str] = []
    current: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        current.append(stripped.removesuffix("\\").strip())
        if not stripped.endswith("\\"):
            logical.append(" ".join(current))
            current = []
    if current:
        raise SystemExit(f"supply-chain audit: unterminated requirement in {path.name}")
    return logical


def _audit_lock(path: Path) -> None:
    requirements = _logical_requirements(path)
    if not requirements:
        raise SystemExit(f"supply-chain audit: empty lock file {path.name}")
    for requirement in requirements:
        if requirement.startswith("-r "):
            continue
        head = requirement.split()[0]
        if "==" not in head:
            raise SystemExit(f"supply-chain audit: unpinned dependency in {path.name}: {head}")
        hashes = [token for token in requirement.split() if token.startswith("--hash=sha256:")]
        if not hashes:
            raise SystemExit(f"supply-chain audit: unhashed dependency in {path.name}: {head}")
        for token in hashes:
            digest = token.removeprefix("--hash=sha256:")
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise SystemExit(f"supply-chain audit: invalid SHA-256 hash in {path.name}: {head}")


def _audit_workflow_actions() -> None:
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        if path.name == "lock-probe.yml":
            raise SystemExit("supply-chain audit: temporary P50 lock probe must not remain")
        for raw in path.read_text(encoding="utf-8").splitlines():
            stripped = raw.strip()
            if not stripped.startswith("- uses:"):
                continue
            target = stripped.split("uses:", 1)[1].strip().split()[0]
            if target.startswith("./"):
                continue
            if "@" not in target:
                raise SystemExit(
                    f"supply-chain audit: external action lacks immutable ref in {path.name}"
                )
            ref = target.rsplit("@", 1)[1]
            if not SHA_REF.fullmatch(ref):
                raise SystemExit(f"supply-chain audit: movable action ref in {path.name}: {target}")


def main() -> int:
    _audit_lock(ROOT / "requirements.lock")
    _audit_lock(ROOT / "requirements-dev.lock")
    _audit_workflow_actions()

    validate = (ROOT / ".github" / "workflows" / "validate.yml").read_text(encoding="utf-8")
    for token in (
        "workflow_call:",
        "validated_sha:",
        "--require-hashes",
        "--only-binary=:all:",
        "--no-build-isolation",
        "--cov-fail-under=68",
        "mypy --follow-imports=skip",
        "Validated SHA",
    ):
        if token not in validate:
            raise SystemExit(f"supply-chain audit: reusable validation gate missing {token}")

    publish = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    for token in (
        "uses: ./.github/workflows/validate.yml",
        "needs.validate.outputs.validated_sha == github.sha",
        "ref: ${{ needs.validate.outputs.validated_sha }}",
        'test "$VALIDATED_SHA" = "$GITHUB_SHA"',
        'test "$(git rev-parse HEAD)" = "$VALIDATED_SHA"',
        "CLASH_RELAY_VALIDATED_SHA: ${{ needs.validate.outputs.validated_sha }}",
    ):
        if token not in publish:
            raise SystemExit(f"supply-chain audit: publication SHA binding missing {token}")

    entrypoint = (ROOT / "scripts" / "run_production_release.py").read_text(encoding="utf-8")
    for token in ("GITHUB_ACTIONS", "GITHUB_SHA", "CLASH_RELAY_VALIDATED_SHA"):
        if token not in entrypoint:
            raise SystemExit(f"supply-chain audit: publication entrypoint missing {token}")

    print("supply-chain audit: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
