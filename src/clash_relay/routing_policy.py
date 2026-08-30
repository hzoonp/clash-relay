"""Post-generation routing policies that reuse existing inline providers."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .errors import GenerationError
from .util import unique

_BUILTINS = {"DIRECT", "REJECT", "PASS", "COMPATIBLE"}


def _source_exclude_pattern(source_ids: tuple[str, ...]) -> str:
    alternatives = "|".join(re.escape(source_id) for source_id in source_ids)
    return rf"^\[[^\]]+\]\s+(?:{alternatives})/"


def _filtered_anchor_name(anchor_name: str, excluded_sources: tuple[str, ...]) -> str:
    material = "\0".join((anchor_name, *excluded_sources))
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
    # Reuse the validator's existing safe shared-anchor prefix.
    return f"__CR_AUTO_FILTER_{digest}"


def apply_acl4ssr_source_exclusions(
    output: dict[str, Any],
    *,
    group_specs: list[dict[str, Any]],
    known_source_ids: set[str],
) -> dict[str, list[str]]:
    """Replace provider-backed policy members with source-filtered hidden anchors."""

    providers = output.get("proxy-providers", {})
    groups = output.get("proxy-groups", [])
    if not isinstance(providers, dict) or not isinstance(groups, list):
        raise GenerationError("generated proxy provider/group structure is invalid")

    by_name: dict[str, dict[str, Any]] = {
        str(group["name"]): group
        for group in groups
        if isinstance(group, dict) and isinstance(group.get("name"), str)
    }
    clone_cache: dict[tuple[str, tuple[str, ...]], str | None] = {}
    fail_closed_cache: dict[tuple[str, ...], str] = {}

    def fail_closed(excluded_sources: tuple[str, ...]) -> str:
        cached = fail_closed_cache.get(excluded_sources)
        if cached is not None:
            return cached
        digest = hashlib.sha256("\0".join(excluded_sources).encode("utf-8")).hexdigest()[:12]
        name = f"__CR_FAIL_CLOSED_FILTER_{digest}"
        group = {
            "name": name,
            "type": "select",
            "hidden": True,
            "proxies": ["REJECT"],
        }
        groups.append(group)
        by_name[name] = group
        fail_closed_cache[excluded_sources] = name
        return name

    def provider_has_survivor(provider_name: str, pattern: re.Pattern[str]) -> bool:
        provider = providers.get(provider_name)
        if not isinstance(provider, dict):
            raise GenerationError(
                f"source-filtered route references unknown provider {provider_name!r}"
            )
        payload = provider.get("payload", [])
        if not isinstance(payload, list):
            return False
        for proxy in payload:
            if not isinstance(proxy, dict):
                continue
            name = proxy.get("name")
            if isinstance(name, str) and not pattern.search(name):
                return True
        return False

    def clone_hidden_anchor(anchor_name: str, excluded_sources: tuple[str, ...]) -> str | None:
        cache_key = (anchor_name, excluded_sources)
        if cache_key in clone_cache:
            return clone_cache[cache_key]

        source = by_name.get(anchor_name)
        if source is None or not source.get("hidden", False):
            raise GenerationError(
                f"source-filtered route requires a hidden routing anchor, got {anchor_name!r}"
            )

        pattern_text = _source_exclude_pattern(excluded_sources)
        pattern = re.compile(pattern_text)
        group_type = source.get("type")
        if group_type == "url-test":
            uses = source.get("use", [])
            if not isinstance(uses, list) or not uses:
                raise GenerationError(
                    f"source-filtered auto group {anchor_name!r} has no provider references"
                )
            if not any(
                provider_has_survivor(str(provider_name), pattern) for provider_name in uses
            ):
                clone_cache[cache_key] = None
                return None
            clone_name = _filtered_anchor_name(anchor_name, excluded_sources)
            clone = dict(source)
            clone["name"] = clone_name
            existing = clone.get("exclude-filter")
            clone["exclude-filter"] = (
                f"(?:{existing})|(?:{pattern_text})"
                if isinstance(existing, str) and existing
                else pattern_text
            )
            groups.append(clone)
            by_name[clone_name] = clone
            clone_cache[cache_key] = clone_name
            return clone_name

        if group_type == "fallback":
            references = source.get("proxies", [])
            if not isinstance(references, list) or not references:
                raise GenerationError(
                    f"source-filtered fallback group {anchor_name!r} has no child groups"
                )
            filtered_children = [
                child
                for child in (
                    clone_hidden_anchor(str(reference), excluded_sources)
                    for reference in references
                )
                if child is not None
            ]
            if not filtered_children:
                clone_cache[cache_key] = None
                return None
            if len(filtered_children) == 1:
                clone_cache[cache_key] = filtered_children[0]
                return filtered_children[0]
            clone_name = _filtered_anchor_name(anchor_name, excluded_sources)
            clone = dict(source)
            clone["name"] = clone_name
            clone["proxies"] = filtered_children
            groups.append(clone)
            by_name[clone_name] = clone
            clone_cache[cache_key] = clone_name
            return clone_name

        if group_type == "select" and source.get("proxies") == ["REJECT"]:
            clone_cache[cache_key] = None
            return None
        raise GenerationError(
            f"source-filtered route cannot clone unsupported anchor type {group_type!r}"
        )

    def filtered_reference(reference: str, excluded_sources: tuple[str, ...]) -> str:
        referenced = by_name.get(reference)
        if referenced is None:
            raise GenerationError(f"source-filtered route references unknown group {reference!r}")
        if referenced.get("hidden", False):
            filtered = clone_hidden_anchor(reference, excluded_sources)
            return filtered or fail_closed(excluded_sources)

        references = referenced.get("proxies", [])
        if not isinstance(references, list) or len(references) != 1:
            raise GenerationError(
                f"source-filtered public group {reference!r} is not backed by one routing anchor"
            )
        hidden_anchor = str(references[0])
        if hidden_anchor not in by_name or not by_name[hidden_anchor].get("hidden", False):
            raise GenerationError(
                f"source-filtered public group {reference!r} does not point to a hidden anchor"
            )
        filtered = clone_hidden_anchor(hidden_anchor, excluded_sources)
        return filtered or fail_closed(excluded_sources)

    report: dict[str, list[str]] = {}
    for spec in group_specs:
        raw_excluded = spec.get("excluded_sources", [])
        if not raw_excluded:
            continue
        excluded_sources = tuple(sorted(str(source_id) for source_id in raw_excluded))
        unknown = set(excluded_sources) - known_source_ids
        if unknown:
            raise GenerationError(
                f"ACL4SSR group {spec['id']!r} excludes unknown subscription sources: "
                + ", ".join(sorted(unknown))
            )
        public_name = str(spec["display_name"])
        public = by_name.get(public_name)
        if public is None or public.get("hidden", False):
            raise GenerationError(
                f"ACL4SSR group {spec['id']!r} did not produce a public policy group"
            )
        references = public.get("proxies", [])
        if not isinstance(references, list) or not references:
            raise GenerationError(f"ACL4SSR group {spec['id']!r} has no routing members")

        rewritten: list[str] = []
        filtered_any = False
        for reference in references:
            name = str(reference)
            if name in _BUILTINS:
                rewritten.append(name)
                continue
            rewritten.append(filtered_reference(name, excluded_sources))
            filtered_any = True
        if not filtered_any:
            raise GenerationError(
                f"ACL4SSR group {spec['id']!r} excludes sources but has no provider-backed member"
            )
        public["proxies"] = unique(rewritten)
        report[public_name] = list(excluded_sources)
    return report
