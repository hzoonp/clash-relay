"""Fetch and normalize pinned ACL4SSR Clash rule fragments."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote

from .errors import FetchError, GenerationError
from .fetch import fetch_subscription

RuleFetcher = Callable[..., str]

_SUPPORTED_TYPES = {
    "DOMAIN",
    "DOMAIN-SUFFIX",
    "DOMAIN-KEYWORD",
    "DOMAIN-REGEX",
    "IP-CIDR",
    "IP-CIDR6",
    "SRC-IP-CIDR",
    "DST-PORT",
    "SRC-PORT",
    "PROCESS-NAME",
    "PROCESS-PATH",
    "NETWORK",
}
# These legacy Clash rule types occur in some ACL4SSR fragments but are not
# accepted by the pinned Mihomo cores used by this project. They are skipped
# explicitly and reported instead of being silently rewritten.
_SKIPPED_TYPES = {"URL-REGEX", "USER-AGENT"}
_ALLOWED_OPTIONS = {"no-resolve"}


def _raw_url(repository: str, ref: str, path: str) -> str:
    encoded = quote(path, safe="/")
    return f"https://raw.githubusercontent.com/{repository}/{ref}/{encoded}"


def parse_acl4ssr_list(text: str, *, source_id: str) -> tuple[list[dict[str, Any]], int]:
    """Parse a classical ACL4SSR .list fragment into clash-relay rule objects."""

    rules: list[dict[str, Any]] = []
    skipped = 0
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            raise GenerationError(
                f"ACL4SSR source {source_id!r} has an invalid rule at line {line_number}"
            )
        rule_type = parts[0].upper()
        if rule_type in _SKIPPED_TYPES:
            skipped += 1
            continue
        if rule_type not in _SUPPORTED_TYPES:
            raise GenerationError(
                f"ACL4SSR source {source_id!r} uses unsupported rule type {rule_type!r} "
                f"at line {line_number}"
            )
        value = parts[1]
        if not value or "\r" in value or "\n" in value:
            raise GenerationError(
                f"ACL4SSR source {source_id!r} has an invalid value at line {line_number}"
            )
        options = parts[2:]
        unknown_options = [option for option in options if option not in _ALLOWED_OPTIONS]
        if unknown_options:
            raise GenerationError(
                f"ACL4SSR source {source_id!r} has unsupported options at line {line_number}"
            )
        rule: dict[str, Any] = {"type": rule_type, "value": value}
        if options:
            rule["options"] = options
        rules.append(rule)
    if not rules:
        raise GenerationError(f"ACL4SSR source {source_id!r} contains no supported rules")
    return rules, skipped


def load_acl4ssr_rules(
    manifest: dict[str, Any] | None,
    *,
    modules: Mapping[str, bool],
    fetcher: RuleFetcher = fetch_subscription,
    timeout: int,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Fetch enabled sources from one immutable ACL4SSR commit."""

    if manifest is None:
        return [], None

    repository = str(manifest["repository"])
    ref = str(manifest["ref"])
    max_source_bytes = int(manifest["max_source_bytes"])
    rows: list[dict[str, Any]] = []
    source_reports: list[dict[str, Any]] = []

    for source in sorted(manifest["sources"], key=lambda item: (item["priority"], item["id"])):
        module = source.get("module")
        if module is not None and not modules.get(str(module), False):
            continue
        source_id = str(source["id"])
        url = _raw_url(repository, ref, str(source["path"]))
        try:
            text = fetcher(
                url,
                timeout=timeout,
                max_bytes=max_source_bytes,
                allow_http=False,
                allow_file=False,
            )
        except FetchError as exc:
            raise GenerationError(f"ACL4SSR source {source_id!r} could not be fetched") from exc
        rules, skipped = parse_acl4ssr_list(text, source_id=source_id)
        for order, rule in enumerate(rules):
            rows.append(
                {
                    "priority": int(source["priority"]),
                    "source_id": source_id,
                    "order": order,
                    "target": str(source["target"]),
                    "rule": rule,
                }
            )
        source_reports.append(
            {
                "id": source_id,
                "path": str(source["path"]),
                "rules": len(rules),
                "skipped_legacy_rules": skipped,
            }
        )

    for inline in sorted(
        manifest.get("inline_rules", []), key=lambda item: (item["priority"], item["id"])
    ):
        module = inline.get("module")
        if module is not None and not modules.get(str(module), False):
            continue
        rule: dict[str, Any] = {"type": str(inline["type"]), "value": inline["value"]}
        if inline.get("options"):
            rule["options"] = list(inline["options"])
        rows.append(
            {
                "priority": int(inline["priority"]),
                "source_id": str(inline["id"]),
                "order": 0,
                "target": str(inline["target"]),
                "rule": rule,
            }
        )

    if not rows:
        raise GenerationError("ACL4SSR routing is enabled but produced no rules")
    report = {
        "repository": repository,
        "ref": ref,
        "license": str(manifest["license"]),
        "sources": source_reports,
        "rules": len(rows),
        "skipped_legacy_rules": sum(item["skipped_legacy_rules"] for item in source_reports),
    }
    return rows, report
