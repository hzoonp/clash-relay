"""Orchestrate the one-way subscription-to-candidate build pipeline."""

from __future__ import annotations

import socket
import ssl
import urllib.error
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .acl4ssr import load_acl4ssr_rules
from .classify import classify_proxy, deduplicate_nodes
from .config_loader import ProjectDefinition, load_project
from .errors import FetchError, GenerationError, SubscriptionError, UnsafeSubscriptionError
from .fetch import fetch_subscription
from .mihomo_serializer import serialize_runtime_graph
from .models import BuildResult, Node, SubscriptionSpec
from .node_policy import filter_proxies_by_multiplier, filter_proxies_by_name_patterns
from .pinned_fetch import fetch_pinned_text
from .policy_compiler import compile_runtime_graph
from .redact import redact_text
from .secrets import resolve_subscription_urls
from .subscription_parser import ParsedSubscription, parse_subscription
from .util import dump_yaml, sha256_text
from .validator import validate_generated_config

Fetcher = Callable[..., str]


def _failure_is_fatal(spec: SubscriptionSpec, project: ProjectDefinition) -> bool:
    return spec.on_error == "fail" or (
        spec.required and project.config["generation"]["fail_on_required_subscription_error"]
    )


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


def _exception_chain(error: BaseException) -> tuple[BaseException, ...]:
    current: BaseException | None = error
    seen: set[int] = set()
    values: list[BaseException] = []
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        values.append(current)
        current = current.__cause__ or current.__context__
    return tuple(values)


def _fetch_failure_reason(error: FetchError) -> str:
    chain = _exception_chain(error)
    if any(isinstance(item, urllib.error.HTTPError) for item in chain):
        return "http_error"
    if any(isinstance(item, ssl.SSLError) for item in chain):
        return "tls_error"
    if any(isinstance(item, TimeoutError | socket.timeout) for item in chain):
        return "timeout"
    if any(isinstance(item, socket.gaierror) for item in chain):
        return "dns_error"

    message = str(error)
    if message.startswith("subscription hostname could not be resolved for "):
        return "dns_error"
    if message == "subscription hostname resolved to no usable address":
        return "dns_error"
    if message in {
        "subscription URL is malformed",
        "subscription URL userinfo is not allowed",
        "subscription URL has no hostname",
        "subscription URL has an invalid port",
    } or message.startswith("subscription URL scheme "):
        return "invalid_url"
    if message in {
        "subscription URL may not target a private or special-use IP literal",
        "subscription hostname may not target localhost",
        "subscription hostname resolves to a private or special-use address",
    }:
        return "destination_rejected"
    if message in {
        "subscription exceeds the configured byte limit",
        "decompressed subscription exceeds the byte limit",
    }:
        return "size_limit"
    if message in {
        "subscription gzip payload is invalid",
        "subscription is not valid UTF-8",
    }:
        return "payload_encoding"
    if message == "cannot read local subscription fixture":
        return "io_error"
    return "transport_error"


def _source_failure_diagnostic(error: BaseException) -> dict[str, str]:
    if isinstance(error, UnsafeSubscriptionError):
        return {
            "failure_category": "subscription_admission",
            "failure_reason": "unsafe_payload",
        }
    if isinstance(error, SubscriptionError):
        return {
            "failure_category": "subscription_parse",
            "failure_reason": (
                "no_usable_proxies"
                if str(error) == "subscription contains no usable proxies"
                else "parse_error"
            ),
        }
    if isinstance(error, FetchError):
        return {
            "failure_category": "subscription_fetch",
            "failure_reason": _fetch_failure_reason(error),
        }
    if isinstance(error, OSError):
        return {"failure_category": "io_failure", "failure_reason": "io_error"}
    return {
        "failure_category": "subscription_parse",
        "failure_reason": "invalid_value",
    }


_EMPTY_SUBSCRIPTION_REASON_BY_INVALID_REASON = {
    "invalid_entry": "all_invalid_entries",
    "invalid_fields": "all_invalid_fields",
    "invalid_name": "all_invalid_names",
    "missing_type": "all_missing_types",
    "unsupported_type": "all_unsupported_types",
    "invalid_server": "all_invalid_servers",
    "invalid_port": "all_invalid_ports",
    "private_host": "all_private_hosts",
    "missing_required_field": "all_missing_required_fields",
    "malformed_options": "all_malformed_options",
    "unsupported_values": "all_unsupported_values",
    "other_invalid": "all_other_invalid",
}


def _empty_subscription_failure_reason(parsed: ParsedSubscription) -> str:
    if parsed.skipped_items <= 0:
        return "empty_subscription"
    reasons = {
        reason
        for reason, count in parsed.skipped_reason_counts
        if isinstance(count, int) and not isinstance(count, bool) and count > 0
    }
    if len(reasons) != 1:
        return "mixed_invalid_proxies"
    reason = next(iter(reasons))
    return _EMPTY_SUBSCRIPTION_REASON_BY_INVALID_REASON.get(reason, "all_other_invalid")


def build_candidate(
    *,
    config_path: Path,
    subscriptions_path: Path,
    policies_path: Path,
    secret_file: Path | None = None,
    env: Mapping[str, str] | None = None,
    fetcher: Fetcher = fetch_subscription,
    rule_fetcher: Fetcher = fetch_pinned_text,
) -> BuildResult:
    project = load_project(
        config_path=config_path,
        subscriptions_path=subscriptions_path,
        policies_path=policies_path,
    )
    enabled_specs = [spec for spec in project.subscriptions if spec.enabled]
    urls, secret_values = resolve_subscription_urls(enabled_specs, secret_file=secret_file, env=env)
    generation = project.config["generation"]
    nodes: list[Node] = []
    source_reports: list[dict[str, Any]] = []
    successful = 0
    name_filtered_nodes = 0
    multiplier_filtered_nodes = 0
    for spec in sorted(enabled_specs, key=lambda item: (item.ingest_order, item.id)):
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
                source_report: dict[str, Any] = {
                    "id": spec.id,
                    "display_name": spec.display_name,
                    "status": "failed",
                    "failure_category": "subscription_parse",
                    "failure_reason": _empty_subscription_failure_reason(parsed),
                    "skipped_invalid_nodes": parsed.skipped_items,
                }
                if parsed.empty_payload_shape is not None:
                    source_report["empty_payload_shape"] = parsed.empty_payload_shape
                source_reports.append(source_report)
                if _failure_is_fatal(spec, project):
                    raise GenerationError(
                        f"subscription {spec.id!r} failed: subscription contains no usable proxies"
                    )
                continue

            admitted_by_name, rejected_name = filter_proxies_by_name_patterns(
                parsed.proxies,
                deny_patterns=spec.deny_name_patterns,
            )
            admitted, rejected_multiplier = filter_proxies_by_multiplier(
                admitted_by_name,
                max_multiplier=spec.max_node_multiplier,
            )
            classified = [classify_proxy(proxy, spec, project.policies) for proxy in admitted]
            nodes.extend(classified)
            successful += 1
            name_filtered_nodes += rejected_name
            multiplier_filtered_nodes += rejected_multiplier

            source_report: dict[str, Any] = {
                "id": spec.id,
                "display_name": spec.display_name,
                "status": "ok",
                "nodes": len(classified),
                "skipped_invalid_nodes": parsed.skipped_items,
                "filtered_by_name": rejected_name,
                "filtered_over_multiplier": rejected_multiplier,
            }
            if spec.max_node_multiplier is not None:
                source_report["max_node_multiplier"] = spec.max_node_multiplier
            source_reports.append(source_report)
        except (FetchError, SubscriptionError, OSError, ValueError) as exc:
            safe_error = redact_text(str(exc), secret_values)
            source_reports.append(
                {
                    "id": spec.id,
                    "display_name": spec.display_name,
                    "status": "failed",
                    **_source_failure_diagnostic(exc),
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
    final_target = (
        str(project.acl4ssr["final_target"])
        if project.acl4ssr and project.acl4ssr.get("final_target")
        else None
    )
    final_excluded_sources = (
        list(project.acl4ssr.get("final_excluded_sources", [])) if project.acl4ssr else []
    )

    compiled = compile_runtime_graph(
        root=project.root,
        config=project.config,
        policies=project.policies,
        nodes=deduplicated,
        known_source_ids={spec.id for spec in enabled_specs},
        external_rule_providers=external_rule_providers,
        external_rules=external_rules,
        acl_groups=acl_groups,
        final_target=final_target,
        final_excluded_sources=final_excluded_sources,
    )
    output = serialize_runtime_graph(compiled.graph)

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
        "name_filtered_nodes": name_filtered_nodes,
        "multiplier_filtered_nodes": multiplier_filtered_nodes,
        **compiled.report,
    }
    if acl_report is not None:
        report["rule_sources"] = {"acl4ssr": acl_report}
    return BuildResult(output, yaml_text, report, secret_values)
