#!/usr/bin/env python3
"""Create only ignored local URL injection for the entirely fictional test project."""

from __future__ import annotations

from pathlib import Path

import yaml


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    output = root / ".work" / "fixture-secrets.yaml"
    output.parent.mkdir(parents=True, exist_ok=True)
    source = root / "tests" / "fixtures" / "subscriptions"
    mapping = {
        "SUB_PRIMARY": (source / "primary.yaml").resolve().as_uri(),
        "SUB_SECONDARY": (source / "secondary.yaml").resolve().as_uri(),
        "SUB_SPECIAL": (source / "special.yaml").resolve().as_uri(),
    }
    output.write_text(yaml.safe_dump(mapping, sort_keys=True), encoding="utf-8")
    output.chmod(0o600)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
