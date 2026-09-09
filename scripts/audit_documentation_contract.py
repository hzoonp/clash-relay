from __future__ import annotations

import sys
from pathlib import Path

import yaml

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
        "Policy Model v2",
        "migrate_policy_v2.py",
    ),
    "docs/quickstart.zh-CN.md": (
        "tools/mihomo-versions.json",
        "previous-release-v1",
        "clash-relay doctor",
        "Policy Model v2",
        "migrate_policy_v2.py",
    ),
}

FORBIDDEN = (
    "Mihomo v1.19.30 plus v1.19.29",
    "Mihomo v1.19.30 / v1.19.29",
    "previous-good snapshot",
    "dual-core validated rollback",
    "services.yaml",
    "Policy Model v1 remains readable",
    "Policy Model v1 仍可",
    "current/deprecated",
    "current` 还是 `deprecated",
    "automatic six-hour production refresh",
    "每 6 小时自动刷新",
    "AI routing continues to exclude CN/HK",
)

PUBLICATION_CONTRACT_DOCS = (
    "README.md",
    "README.zh-CN.md",
    "docs/quickstart.md",
    "docs/quickstart.zh-CN.md",
    "docs/publishing.md",
    "docs/production-cutover.md",
    "docs/releases/2.2.0.md",
)

PUBLIC_SURFACE_DOCS = (
    "README.md",
    "README.zh-CN.md",
    "docs/quickstart.md",
    "docs/quickstart.zh-CN.md",
    "docs/routing-v2.md",
)


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _canonical_visible_groups() -> tuple[str, ...]:
    manifest = yaml.safe_load(_read("rules/acl4ssr.yaml"))
    return tuple(
        str(group["display_name"])
        for group in manifest["groups"]
        if not bool(group.get("hidden", False))
    )


def _canonical_ai_excluded_regions() -> frozenset[str]:
    topology = yaml.safe_load(_read("policies/topology.yaml"))
    pools = topology["pools"]
    browsing_regions = {
        str(region)
        for pool in pools
        if pool["source_use"] == "browsing"
        for region in pool["regions"]
        if str(region) != "ANY"
    }
    ai_regions = {
        str(region)
        for pool in pools
        if pool["source_use"] == "ai"
        for region in pool["regions"]
        if str(region) != "ANY"
    }
    return frozenset(browsing_regions - ai_regions)


def audit(root: Path = ROOT) -> list[str]:
    global ROOT
    original_root = ROOT
    ROOT = root
    try:
        errors: list[str] = []
        texts: dict[str, str] = {}

        for relative in set(AUTHORITATIVE) | set(PUBLICATION_CONTRACT_DOCS) | set(PUBLIC_SURFACE_DOCS):
            try:
                texts[relative] = _read(relative)
            except OSError:
                errors.append(f"missing authoritative documentation: {relative}")

        for relative in AUTHORITATIVE:
            text = texts.get(relative, "")
            for token in REQUIRED[relative]:
                if token not in text:
                    errors.append(f"{relative} is missing current contract token: {token}")
            for token in FORBIDDEN:
                if token in text:
                    errors.append(f"{relative} contains stale contract wording: {token}")

        for relative in PUBLICATION_CONTRACT_DOCS:
            text = texts.get(relative, "")
            for token in ("push", "schedule", "dry-run", "publish=true"):
                if token not in text:
                    errors.append(
                        f"{relative} does not explicitly describe the current publication trigger contract: {token}"
                    )
            for token in FORBIDDEN:
                if token in text:
                    errors.append(f"{relative} contains stale contract wording: {token}")

        try:
            visible_groups = _canonical_visible_groups()
        except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
            errors.append(f"cannot derive canonical public surface: {exc}")
            visible_groups = ()
        for relative in PUBLIC_SURFACE_DOCS:
            text = texts.get(relative, "")
            for group in visible_groups:
                if group not in text:
                    errors.append(f"{relative} is missing canonical visible group: {group}")

        try:
            excluded_regions = _canonical_ai_excluded_regions()
        except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
            errors.append(f"cannot derive canonical AI excluded regions: {exc}")
            excluded_regions = frozenset()
        if excluded_regions != frozenset({"HK"}):
            errors.append(
                "canonical topology no longer has the reviewed HK-only AI exclusion; "
                f"found {sorted(excluded_regions)}"
            )
        routing_text = texts.get("docs/routing-v2.md", "")
        if "excluded: HK" not in routing_text:
            errors.append("docs/routing-v2.md is missing canonical `excluded: HK` policy wording")

        return errors
    finally:
        ROOT = original_root


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
