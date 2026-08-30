"""Orchestrate the one-way subscription-to-candidate build pipeline."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .acl4ssr import load_acl4ssr_rules
from .classify import classify_proxy, deduplicate_nodes
from .config_loader import ProjectDefinition, load_project
from .errors import FetchError, GenerationError, SubscriptionError
from .fetch import fetch_subscription
from .generator import generate_config
from .models import BuildResult, Node, SubscriptionSpec
from .node_policy import filter_proxies_by_multiplier
from .redact import redact_text
from .secrets import resolve_subscription_urls
from .subscription_parser import parse_subscription
from .util import dump_yaml, sha256_text, unique
from .validator import validate_generated_config

Fetcher = Callable[..., str]
_BUILTINS = {"DIRECT", "REJECT", "PASS", "COMPATIBLE"}


def _failure_is_fatal(spec: SubscriptionSpec, project: ProjectDefinition) -> bool:
    return spec.on_error == "fail" or (
        spec.required and project.config["generation"]["fail_on_required_subscription_error"]
    )


def _expose_manual_provider_choices(
    output: dict[str, Any], *, excluded_groups: set[str] | None = None
) -> None:
    """Let node-owning groups expose providers without altering policy-only groups."""

    excluded = excluded_groups or set()
    providers = output.get("proxy-providers", {})
    groups = output.get("proxy-groups", [])
    if not isinstance(providers, dict) or not isinstance(groups, list):
        return

    by_name = {
        group["name"]: group
        for group in groups
        if isinstance(group, dict) and isinstance(group.get("name"), str)
    }

    def provider_names_from_anchor(anchor_name: str) -> list[str]:
        found: list[str] = []
        pending = [anchor_name]
        visited: set[str] = set()
        while pending:
            group_name = pending.pop(0)
            if group_name in visited:
                continue
            visited.add(group_name)
            group = by_name.get(group_name)
            if not isinstance(group, dict):
                continue
            uses = group.get("use", [])
            if isinstance(uses, list):
                found.extend(name for name in uses if isinstance(name, str) and name in providers)
            references = group.get("proxies", [])
            if isinstance(references, list):
                pending.extend(
                    name for name in references if isinstance(name, str) and name in by_name
                )
        return unique(found)

    for public in groups:
        if not isinstance(public, dict) or public.get("hidden", False):
            continue
        if public.get("name") in excluded:
            continue
        references = public.get("proxies", [])
        if not isinstance(references, list) or len(references) != 1:
            continue
        provider_names = provider_names_from_anchor(str(references[0]))
        if provider_names:
            public["use"] = provider_names


def _source_exclude_pattern(source_ids: tuple[str, ...]) -> str:
    alternatives = "|".join(re.escape(source_id) for source_id in source_ids)
    return rf"^\[[^\]]+\]\s+(?:{alternatives})/"


def _apply_acl4ssr_source_exclusions(
    output: dict[str, Any],
    *,
    group_specs: list[dict[str, Any]],
    known_source_ids: set[str],
) -> dict[str, list[str]]:
    """Replace provider-backed group members with source-filtered hidden anchors."""

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
        group = {"name": name, "type": "select", "hidden": True, "proxies": ["REJECT"]}
        groups.append(group)
        by_name[name] = group
        fail_closed_cache[excluded_sources] = name
        return name

    def provider_has_survivor(provider_name: str, pattern: re.Pattern[str]) -> bool:
        provider = providers.get(provider_name)
        if not isinstance(provider, dict):
            raise GenerationError(f"source-filtered route references unknown provider {provider_name!r}")
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

    def clone_hidden_anchor(
        anchor_name: str, excluded_sources: tuple[str, ...]
    ) -> str | None:
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
            digest = hashlib.sha256(
                f"{anchor_name}\0{'\0'.join(excluded_sources)}".encode("utf-8")
            ).hexdigest()[:12]
            clone_name = f"__CR_FILTER_{digest}"
            clone = dict(source)
            clone["name"] = clone_name
            existing = clone.get("exclude-filter")
            clone["exclude-filter"] = (
                f"(?:{existing})|(?:{pattern_text})" if isinstance(existing, str) and existing else pattern_text
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
            digest = hashlib.sha256(
                f"{anchor_name}\0{'\0'.join(excluded_sources)}".encode("utf-8")
            ).hexdigest()[:12]
            clone_name = f"__CR_FILTER_{digest}"
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


def _with_acl4ssr_attribution(
    yaml_text: str, *, generated_header: bool, acl_report: dict[str, Any] | None
) -> str:
    if acl_report is None:
        return yaml_text
    attribution = (
        "# ACL4SSR routing data adapted by clash-relay from "
        f"ACL4SSR/ACL4SSR@{acl_report['ref']} under CC-BY-SA-4.0; "
        "https://creativecommons.org/licenses/by-sa/4.0/"
    )
    if generated_header and "\n" in yaml_text:
        first, rest = yaml_text.split("\n", 1)
        return f"{first}\n{attribution}\n{rest}"
    return f"{attribution}\n{yaml_text}"


def build_candidate(
    *,
    config_path: Path,
    subscriptions_path: Path,
    services_path: Path,
    policies_path: Path,
    secret_file: Path | None = None,
    env: Mapping[str, str] | None = None,
    fetcher: Fetcher = fetch_subscription,
    rule_fetcher: Fetcher = fetch_subscription,
) -> BuildResult:
    project = load_project(
        config_path=config_path,
        subscriptions_path=subscriptions_path,
        services_path=services_path,
        policies_path=policies_path,
    )
    enabled_specs = [spec for spec in project.subscriptions if spec.enabled]
    urls, secret_values = resolve_subscription_urls(enabled_specs, secret_file=secret_file, env=env)
    generation = project.config["generation"]
    subscription_rows = {
        str(row["id"]): row for row in project.subscriptions_document["subscriptions"]
    }
    nodes: list[Node] = []
    source_reports: list[dict[str, Any]] = []
    successful = 0
    multiplier_filtered_nodes = 0
    for spec in sorted(enabled_specs, key=lambda item: (item.priority, item.id)):
        try:
            text = fetcher(
                urls[spec.id],
                timeout=generation["fetch_timeout_seconds"],
                max_bytes=generation["max_subscription_bytes"],
                allow_http=generation["allow_http_subscription_urls"],
                allow_file=generation["allow_file_subscription_urls"],
            )
            parsed = parse_subscription(
                text,
                invalid_policy=generation["invalid_proxy_policy"],
                reject_private_hosts=generation["reject_private_proxy_hosts"],
            )
            if not parsed.proxies:
                raise SubscriptionError("subscription contains no usable proxies")
            row = subscription_rows[spec.id]
            raw_ceiling = row.get("max_node_multiplier")
            max_multiplier = float(raw_ceiling) if raw_ceiling is not None else None
            admitted, rejected_multiplier = filter_proxies_by_multiplier(
                parsed.proxies,
                max_multiplier=max_multiplier,
            )
            classified = [classify_proxy(proxy, spec, project.policies) for proxy in admitted]
            nodes.extend(classified)
            successful += 1
            multiplier_filtered_nodes += rejected_multiplier
            source_report: dict[str, Any] = {
                "id": spec.id,
                "display_name": spec.display_name,
                "status": "ok",
                "nodes": len(classified),
                "skipped_invalid_nodes": parsed.skipped_items,
                "filtered_over_multiplier": rejected_multiplier,
            }
            if max_multiplier is not None:
                source_report["max_node_multiplier"] = max_multiplier
            source_reports.append(source_report)
        except (FetchError, SubscriptionError, OSError, ValueError) as exc:
            safe_error = redact_text(str(exc), secret_values)
            source_reports.append(
                {
                    "id": spec.id,
                    "display_name": spec.display_name,
                    "status": "failed",
                    "error": safe_error,
                }
            )
            if _failure_is_fatal(spec, project):
                raise GenerationError(f"subscription {spec.id!r} failed: {safe_error}") from exc

    if successful < generation["minimum_successful_subscriptions"]:
        raise GenerationError(
            "successful subscriptions are below generation.minimum_successful_subscriptions"
        )
    deduplicated, duplicate_count = deduplicate_nodes(nodes, generation["duplicate_policy"])
    if len(deduplicated) < generation["minimum_usable_nodes"]:
        raise GenerationError("usable nodes are below generation.minimum_usable_nodes")

    external_rule_providers, external_rules, acl_report = load_acl4ssr_rules(
        project.acl4ssr,
        modules=project.config["modules"],
        fetcher=rule_fetcher,
        timeout=generation["fetch_timeout_seconds"],
    )
    acl_groups = list(project.acl4ssr.get("groups", [])) if project.acl4ssr else []
    acl_group_names = {str(item["display_name"]) for item in acl_groups}
    final_target = (
        str(project.acl4ssr["final_target"])
        if project.acl4ssr and project.acl4ssr.get("final_target")
        else None
    )
    output, generator_report = generate_config(
        root=project.root,
        config=project.config,
        services=project.services,
        policies=project.policies,
        nodes=deduplicated,
        external_rule_providers=external_rule_providers,
        external_rules=external_rules,
        external_groups=acl_groups,
        final_target=final_target,
    )
    source_exclusions = _apply_acl4ssr_source_exclusions(
        output,
        group_specs=acl_groups,
        known_source_ids={spec.id for spec in enabled_specs},
    )
    _expose_manual_provider_choices(output, excluded_groups=acl_group_names)
    validate_generated_config(output, secret_urls=secret_values)
    yaml_text = dump_yaml(output, header=generation["generated_header"])
    yaml_text = _with_acl4ssr_attribution(
        yaml_text,
        generated_header=generation["generated_header"],
        acl_report=acl_report,
    )
    for value in secret_values:
        if value and value in yaml_text:
            raise GenerationError("a subscription URL secret leaked into candidate YAML")
    report: dict[str, Any] = {
        "schema_version": 1,
        "candidate_sha256": sha256_text(yaml_text),
        "subscriptions": source_reports,
        "successful_subscriptions": successful,
        "parsed_nodes": len(nodes),
        "usable_nodes": len(deduplicated),
        "duplicates_removed": duplicate_count,
        "multiplier_filtered_nodes": multiplier_filtered_nodes,
        **generator_report,
    }
    if acl_report is not None:
        report["rule_sources"] = {"acl4ssr": acl_report}
    if source_exclusions:
        report["source_exclusions"] = source_exclusions
    return BuildResult(output, yaml_text, report, secret_values)
