"""Fetch and normalize pinned ACL4SSR Clash rule fragments."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

from .errors import FetchError, GenerationError
from .fetch import fetch_subscription
from .util import unique

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
# These legacy Clash rule types are present in some ACL4SSR Full fragments but
# are not accepted by the pinned Mihomo cores. Canonical production may omit
# them only when the same immutable ACL4SSR commit explicitly comments out the
# exact rule in its maintained Clash Provider representation.
_LEGACY_TYPES = {"URL-REGEX", "USER-AGENT"}
_ALLOWED_OPTIONS = {"no-resolve"}


def _raw_url(repository: str, ref: str, path: str) -> str:
    encoded = quote(path, safe="/")
    return f"https://raw.githubusercontent.com/{repository}/{ref}/{encoded}"


def _rule_provider_name(source_id: str) -> str:
    return f"acl4ssr_{source_id}"


def _render_classical_rule(rule: dict[str, Any]) -> str:
    parts = [str(rule["type"]), str(rule["value"])]
    parts.extend(str(option) for option in rule.get("options", []))
    return ",".join(parts)


def _parse_acl4ssr_list(
    text: str, *, source_id: str
) -> tuple[list[dict[str, Any]], list[str]]:
    rules: list[dict[str, Any]] = []
    legacy_rules: list[str] = []
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
        if rule_type in _LEGACY_TYPES:
            legacy_rules.append(line)
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
    return rules, legacy_rules


def parse_acl4ssr_list(text: str, *, source_id: str) -> tuple[list[dict[str, Any]], int]:
    """Parse one ACL4SSR .list fragment and report legacy compatibility rules."""

    rules, legacy_rules = _parse_acl4ssr_list(text, source_id=source_id)
    return rules, len(legacy_rules)


def _commented_legacy_rules(text: str) -> set[str]:
    comments: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("#"):
            continue
        candidate = line[1:].strip()
        if not candidate or "," not in candidate:
            continue
        rule_type = candidate.split(",", 1)[0].strip().upper()
        if rule_type in _LEGACY_TYPES:
            comments.add(candidate)
    return comments


def _default_compatibility_path(source_path: str) -> str:
    path = PurePosixPath(source_path)
    return str(PurePosixPath("Clash/Providers") / f"{path.stem}.yaml")


def _validated_compatibility_path(path_text: str, *, source_id: str) -> str:
    path = PurePosixPath(path_text)
    if path.is_absolute() or ".." in path.parts or not path_text.startswith("Clash/Providers/"):
        raise GenerationError(
            f"ACL4SSR source {source_id!r} has an unsafe Mihomo compatibility provider path"
        )
    return path_text


def _verify_legacy_compatibility(
    *,
    source: dict[str, Any],
    source_id: str,
    legacy_rules: list[str],
    repository: str,
    ref: str,
    fetcher: RuleFetcher,
    timeout: int,
    max_source_bytes: int,
) -> str | None:
    if not legacy_rules:
        return None

    source_path = str(source["path"])
    compatibility_path = source.get("mihomo_compatibility_path")
    if not isinstance(compatibility_path, str) or not compatibility_path:
        compatibility_path = _default_compatibility_path(source_path)
    compatibility_path = _validated_compatibility_path(
        compatibility_path, source_id=source_id
    )
    compatibility_url = _raw_url(repository, ref, compatibility_path)
    try:
        compatibility_text = fetcher(
            compatibility_url,
            timeout=timeout,
            max_bytes=max_source_bytes,
            allow_http=False,
            allow_file=False,
        )
    except FetchError as exc:
        raise GenerationError(
            f"ACL4SSR source {source_id!r} contains Mihomo-incompatible legacy rules, "
            "but its pinned ACL4SSR compatibility provider could not be fetched"
        ) from exc

    documented_omissions = _commented_legacy_rules(compatibility_text)
    missing = [rule for rule in legacy_rules if rule not in documented_omissions]
    if missing:
        raise GenerationError(
            f"ACL4SSR source {source_id!r} contains legacy rules that are not explicitly "
            "omitted by its pinned ACL4SSR Mihomo/Clash compatibility provider"
        )
    return compatibility_path


def load_acl4ssr_rules(
    manifest: dict[str, Any] | None,
    *,
    modules: Mapping[str, bool],
    fetcher: RuleFetcher = fetch_subscription,
    timeout: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    """Fetch enabled sources and package them as inline Mihomo rule providers."""

    if manifest is None:
        return {}, [], None

    repository = str(manifest["repository"])
    ref = str(manifest["ref"])
    max_source_bytes = int(manifest["max_source_bytes"])
    providers: dict[str, Any] = {}
    directives: list[dict[str, Any]] = []
    source_reports: list[dict[str, Any]] = []
    total_rules = 0

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
        rules, legacy_rules = _parse_acl4ssr_list(text, source_id=source_id)
        compatibility_path = _verify_legacy_compatibility(
            source=source,
            source_id=source_id,
            legacy_rules=legacy_rules,
            repository=repository,
            ref=ref,
            fetcher=fetcher,
            timeout=timeout,
            max_source_bytes=max_source_bytes,
        )
        payload = unique(_render_classical_rule(rule) for rule in rules)
        if not payload:
            raise GenerationError(f"ACL4SSR source {source_id!r} produced an empty rule provider")
        provider_name = _rule_provider_name(source_id)
        providers[provider_name] = {
            "type": "inline",
            "behavior": "classical",
            "payload": payload,
        }
        directive: dict[str, Any] = {
            "priority": int(source["priority"]),
            "source_id": source_id,
            "order": 0,
            "target": str(source["target"]),
            "provider": provider_name,
        }
        if source.get("excluded_sources"):
            directive["excluded_sources"] = list(source["excluded_sources"])
        directives.append(directive)
        total_rules += len(payload)
        source_report: dict[str, Any] = {
            "id": source_id,
            "path": str(source["path"]),
            "provider": provider_name,
            "rules": len(payload),
            "verified_compatibility_omissions": len(legacy_rules),
        }
        if compatibility_path is not None:
            source_report["mihomo_compatibility_path"] = compatibility_path
        if source.get("excluded_sources"):
            source_report["excluded_sources"] = list(source["excluded_sources"])
        source_reports.append(source_report)

    for inline in sorted(
        manifest.get("inline_rules", []), key=lambda item: (item["priority"], item["id"])
    ):
        module = inline.get("module")
        if module is not None and not modules.get(str(module), False):
            continue
        rule: dict[str, Any] = {"type": str(inline["type"]), "value": inline["value"]}
        if inline.get("options"):
            rule["options"] = list(inline["options"])
        directives.append(
            {
                "priority": int(inline["priority"]),
                "source_id": str(inline["id"]),
                "order": 0,
                "target": str(inline["target"]),
                "rule": rule,
            }
        )
        total_rules += 1

    if not directives:
        raise GenerationError("ACL4SSR routing is enabled but produced no rules")
    report = {
        "repository": repository,
        "ref": ref,
        "license": str(manifest["license"]),
        "sources": source_reports,
        "rule_providers": len(providers),
        "rules": total_rules,
        "verified_compatibility_omissions": sum(
            item["verified_compatibility_omissions"] for item in source_reports
        ),
        "unverified_legacy_rules": 0,
    }
    return providers, directives, report
