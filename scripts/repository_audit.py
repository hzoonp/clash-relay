#!/usr/bin/env python3
"""Fail before the first commit when tracked content resembles private material."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_BASENAMES = {
    ".env",
    "secrets.yaml",
    "secrets.yml",
    "secrets.json",
    "subscriptions.secret.yaml",
    "subscriptions.secret.yml",
    "subscriptions.secret.json",
}
TOKEN_PATTERNS = {
    "GitHub token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    "fine-grained GitHub token": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
    "subscription query secret": re.compile(
        r"(?i)https?://[^\s'\"<>]+[?&](?:token|access_token|apikey|api_key|key|auth|secret)=[^\s&'\"<>]+"
    ),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def repository_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [ROOT / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def main() -> int:
    failures: list[str] = []
    for path in repository_files():
        relative = path.relative_to(ROOT)
        if path.name in FORBIDDEN_BASENAMES:
            failures.append(f"forbidden tracked filename: {relative}")
        if relative.parts and relative.parts[0] == "dist" and path.name != ".gitkeep":
            failures.append(f"generated private output is tracked: {relative}")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            failures.append(f"cannot read {relative}: {exc}")
            continue
        if b"\0" in raw:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in TOKEN_PATTERNS.items():
            if pattern.search(text):
                failures.append(f"{label} found in {relative}")
        if relative in {Path("config.example.yaml"), Path("subscriptions.example.yaml")}:
            if "external-controller:" in text or "secret:" in text:
                failures.append(f"private controller material found in {relative}")
    if failures:
        raise SystemExit("repository safety audit failed:\n- " + "\n- ".join(failures))
    print(f"repository safety audit passed ({len(repository_files())} files checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
