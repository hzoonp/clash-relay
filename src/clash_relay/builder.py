"""Orchestrate the one-way subscription-to-candidate build pipeline."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .acl4ssr import load_acl4ssr_rules
from .acl4ssr_policy import apply_acl4ssr_group_semantics
from .browsing_runtime import harden_browsing_runtime, validate_browsing_public_surface
from .classify import classify_proxy, deduplicate_nodes
from .config_loader import ProjectDefinition, load_project
from .errors import FetchError, GenerationError, SubscriptionError
from .fetch import fetch_subscription
from .generator import generate_config
from .models import BuildResult, Node, SubscriptionSpec
from .node_policy import filter_proxies_by_multiplier
from .redact import redact_text
from .routing_policy import apply_acl4ssr_source_exclusions
from .runtime_graph import RuntimeGraph
from .secrets import resolve_subscription_urls
from .subscription_parser import parse_subscription
from .util import dump_yaml, sha256_text
from .validator import validate_generated_config

Fetcher = Callable[..., str]


def _failure_is_fatal(spec: SubscriptionSpec, project: ProjectDefinition) -> bool:
    return spec.on_error == "fail" or (
        spec.required and project.config["generation"]["fail_on_required_subscription_error"]
    )


def _expose_manual_provider_choices(
    output: dict[str, Any], *, excluded_groups: set[str] | None = None
) -> None:
    """Let node-owning groups expose providers without altering policy-only groups.

    Provider reachability and deterministic traversal order are derived from the
    canonical RuntimeGraph rather than a builder-local traversal implementation.
    """

    excluded = excluded_groups or set()
    groups = output.get("proxy-groups", [])
    if not isinstance(groups, list):
        return
    graph = RuntimeGraph.from_candidate(output)

    for public in groups:
        if not isinstance(public, dict) or public.get("hidden", False):
            continue
        if public.get("name") in excluded:
            continue
        references = public.get("proxies", [])
        if not isinstance(references, list) or len(references) != 1:
            continue
        provider_names = list(graph.provider_order(str(references[0])))
        if provider_names:
            public["use"] = provider_names


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
    policies_path: Path,
    secret_file: Path | None = None,
    env: Mapping[str, str] | None = None,
    fetcher: Fetcher = fetch_subscription,
    rule_fetcher: Fetcher = fetch_subscription,
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

            admitted, rejected_multiplier = filter_proxies_by_multiplier(
                parsed.proxies,
                max_multiplier=spec.max_node_multiplier,
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
    final_excluded_sources = (
        list(project.acl4ssr.get("final_excluded_sources", [])) if project.acl4ssr else []
    )
    output, generator_report = generate_config(
        root=project.root,
        config=project.config,
        policies=project.policies,
        nodes=deduplicated,
        external_rule_providers=external_rule_providers,
        external_rules=external_rules,
        external_groups=acl_groups,
        final_target=final_target,
    )
    acl_group_semantics = (
        apply_acl4ssr_group_semantics(
            output,
            group_specs=acl_groups,
            pool_specs=list(project.policies["pools"]),
        )
        if acl_groups
        else {}
    )
    source_exclusions = apply_acl4ssr_source_exclusions(
        output,
        group_specs=acl_groups,
        known_source_ids={spec.id for spec in enabled_specs},
        rule_specs=external_rules,
        final_target=final_target,
        final_excluded_sources=final_excluded_sources,
    )
    _expose_manual_provider_choices(output, excluded_groups=acl_group_names)
    browsing_runtime = (
        harden_browsing_runtime(output, project.policies)
        if acl_groups
        else {"status": "not_applicable"}
    )
    validate_browsing_public_surface(output)
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
    if acl_group_semantics:
        report["acl4ssr_groups"] = acl_group_semantics
    if source_exclusions:
        report["source_exclusions"] = source_exclusions
    if browsing_runtime.get("status") != "not_applicable":
        report["browsing_runtime"] = browsing_runtime
    return BuildResult(output, yaml_text, report, secret_values)
