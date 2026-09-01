from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

AUTHORITATIVE = (
    "README.md",
    "README.zh-CN.md",
    "docs/quickstart.md",
    "docs/quickstart.zh-CN.md",
)

REQUIRED = {
    "README.md": (
        "tools/mihomo-versions.json",
        "versioned Cloudflare KV release transaction",
        "clash-relay doctor",
    ),
    "README.zh-CN.md": (
        "tools/mihomo-versions.json",
        "versioned Cloudflare KV release transaction",
        "clash-relay doctor",
    ),
    "docs/quickstart.md": (
        "tools/mihomo-versions.json",
        "previous-release-v1",
        "clash-relay doctor",
    ),
    "docs/quickstart.zh-CN.md": (
        "tools/mihomo-versions.json",
        "previous-release-v1",
        "clash-relay doctor",
    ),
}

FORBIDDEN = (
    "Mihomo v1.19.30 plus v1.19.29",
    "Mihomo v1.19.30 / v1.19.29",
    "previous-good snapshot",
    "dual-core validated rollback",
)


def audit(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    for relative in AUTHORITATIVE:
        path = root / relative
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            errors.append(f"missing authoritative documentation: {relative}")
            continue
        for token in REQUIRED[relative]:
            if token not in text:
                errors.append(f"{relative} is missing current contract token: {token}")
        for token in FORBIDDEN:
            if token in text:
                errors.append(f"{relative} contains stale contract wording: {token}")
    return errors


def main() -> int:
    errors = audit()
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 2
    print("documentation contract: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
