from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

GUIDES = (
    "README.md",
    "README.zh-CN.md",
    "docs/quickstart.md",
    "docs/quickstart.zh-CN.md",
)
CANONICAL = (
    "docs/publishing.md",
    "docs/routing-v2.md",
    "docs/rules.md",
)
OPERATIONS = ("docs/production-cutover.md",)
FORBIDDEN = (
    "services.yaml",
    "Policy Model v1 remains readable",
    "Policy Model v1 仍可",
    "Automatic `push` and `schedule` production runs are hard-latched to dry-run mode.",
    "Automatic `push` and `schedule` events are hard-latched to dry-run mode.",
    "自动 `push` 和 `schedule` 生产运行都被硬锁为 dry-run",
    "自动 `push` 与 `schedule` 事件始终被硬锁为 dry-run",
    "Automatic `push` remains dry-run",
    "Automatic `push` executions are fail-closed to `publish=false`.",
    "push-triggered production workflow is therefore the preferred first production-parity dry run.",
    "`push` remains dry-run even if a publish-like environment value is present.",
)


def _read(root: Path, relative: str) -> str:
    return (root / relative).read_text(encoding="utf-8")


def _canonical_visible_groups(root: Path) -> tuple[str, ...]:
    manifest = yaml.safe_load(_read(root, "rules/acl4ssr.yaml"))
    return tuple(
        str(group["display_name"])
        for group in manifest["groups"]
        if not bool(group.get("hidden", False))
    )


def _canonical_source_uses(root: Path) -> dict[str, tuple[str, ...]]:
    document = yaml.safe_load(_read(root, "subscriptions.yaml"))
    return {
        str(subscription["id"]): tuple(str(use) for use in subscription["allowed_uses"])
        for subscription in document["subscriptions"]
        if bool(subscription.get("enabled", False))
    }


def _canonical_ai_excluded_regions(root: Path) -> frozenset[str]:
    topology = yaml.safe_load(_read(root, "policies/topology.yaml"))
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
    errors: list[str] = []
    texts: dict[str, str] = {}
    for relative in (*GUIDES, *CANONICAL, *OPERATIONS):
        try:
            texts[relative] = _read(root, relative)
        except OSError:
            errors.append(f"missing documentation: {relative}")

    for relative in GUIDES:
        text = texts.get(relative, "")
        if "clash-relay doctor" not in text:
            errors.append(f"{relative} does not surface the supported doctor entrypoint")
        for target in ("publishing.md", "routing-v2.md", "rules.md"):
            if target not in text:
                errors.append(f"{relative} does not link to canonical {target}")

    publishing = texts.get("docs/publishing.md", "")
    for token in ("push", "schedule", "dry-run", "publish=true", "CLASH_RELAY_SCHEDULE_PUBLISH"):
        if token not in publishing:
            errors.append(f"docs/publishing.md is missing publication contract token: {token}")

    cutover = texts.get("docs/production-cutover.md", "")
    for token in (
        "A `main` push runs only the reusable code-validation matrix.",
        "does not enter the production lifecycle",
        "`workflow_dispatch` with `publish=false`",
        "zero-write production-parity dry run",
    ):
        if token not in cutover:
            errors.append(
                f"docs/production-cutover.md is missing exact-SHA dry-run contract token: {token}"
            )

    try:
        visible_groups = _canonical_visible_groups(root)
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        errors.append(f"cannot derive canonical public surface: {exc}")
        visible_groups = ()
    routing = texts.get("docs/routing-v2.md", "")
    for group in visible_groups:
        if group not in routing:
            errors.append(f"docs/routing-v2.md is missing canonical visible group: {group}")

    try:
        source_uses = _canonical_source_uses(root)
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        errors.append(f"cannot derive canonical source policy: {exc}")
        source_uses = {}
    rules = texts.get("docs/rules.md", "")
    for source_id, allowed_uses in source_uses.items():
        expected = f"{source_id}\n  allowed_uses: {', '.join(allowed_uses)}"
        if expected not in rules:
            errors.append(
                f"docs/rules.md is missing canonical source policy for {source_id}: "
                f"allowed_uses={list(allowed_uses)}"
            )

    try:
        excluded_regions = _canonical_ai_excluded_regions(root)
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        errors.append(f"cannot derive canonical AI excluded regions: {exc}")
        excluded_regions = frozenset()
    if excluded_regions != frozenset({"HK", "UK"}):
        errors.append(
            "canonical topology no longer has the reviewed HK/UK AI exclusion; "
            f"found {sorted(excluded_regions)}"
        )
    if "excluded: HK, UK" not in routing:
        errors.append("docs/routing-v2.md is missing canonical `excluded: HK, UK` wording")

    for relative, text in texts.items():
        for token in FORBIDDEN:
            if token in text:
                errors.append(f"{relative} contains stale contract wording: {token}")

    return errors


def main() -> int:
    errors = audit()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("documentation contract: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
