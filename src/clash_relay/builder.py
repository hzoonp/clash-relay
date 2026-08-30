"""Orchestrate the one-way subscription-to-candidate build pipeline."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .classify import classify_proxy, deduplicate_nodes
from .config_loader import ProjectDefinition, load_project
from .errors import FetchError, GenerationError, SubscriptionError
from .fetch import fetch_subscription
from .generator import generate_config
from .models import BuildResult, Node, SubscriptionSpec
from .redact import redact_text
from .secrets import resolve_subscription_urls
from .subscription_parser import parse_subscription
from .util import dump_yaml, sha256_text
from .validator import validate_generated_config

Fetcher = Callable[..., str]


def _failure_is_fatal(spec: SubscriptionSpec, project: ProjectDefinition) -> bool:
    return spec.on_error == "fail" or (
        spec.required and project.config["generation"]["fail_on_required_subscription_error"]
    )


def build_candidate(
    *,
    config_path: Path,
    subscriptions_path: Path,
    services_path: Path,
    policies_path: Path,
    secret_file: Path | None = None,
    env: Mapping[str, str] | None = None,
    fetcher: Fetcher = fetch_subscription,
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
    nodes: list[Node] = []
    source_reports: list[dict[str, Any]] = []
    successful = 0
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
            classified = [classify_proxy(proxy, spec, project.policies) for proxy in parsed.proxies]
            nodes.extend(classified)
            successful += 1
            source_reports.append(
                {
                    "id": spec.id,
                    "display_name": spec.display_name,
                    "status": "ok",
                    "nodes": len(classified),
                    "skipped_invalid_nodes": parsed.skipped_items,
                }
            )
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
    output, generator_report = generate_config(
        root=project.root,
        config=project.config,
        services=project.services,
        policies=project.policies,
        nodes=deduplicated,
    )
    validate_generated_config(output, secret_urls=secret_values)
    yaml_text = dump_yaml(output, header=generation["generated_header"])
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
        **generator_report,
    }
    return BuildResult(output, yaml_text, report, secret_values)
