"""Compile declarations and node inventory into the final runtime graph.

This module owns all topology-changing passes that must happen before Mihomo
serialization.  Callers receive a frozen logical RuntimeGraph plus aggregate
compiler evidence; they must not patch proxy groups/providers afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .acl4ssr_policy import apply_acl4ssr_group_semantics
from .browsing_runtime import harden_browsing_runtime, validate_browsing_public_surface
from .generator import generate_config
from .models import Node
from .routing_policy import apply_acl4ssr_source_exclusions
from .runtime_graph import RuntimeGraph


@dataclass(frozen=True, slots=True)
class CompiledRuntime:
    """Final logical runtime graph and privacy-safe compiler evidence."""

    graph: RuntimeGraph
    report: dict[str, Any]


def _expose_manual_provider_choices(
    output: dict[str, Any], *, excluded_groups: set[str]
) -> dict[str, Any]:
    """Expose provider inventory only on node-owning public selectors.

    Provider traversal is derived from RuntimeGraph, keeping graph ownership in
    one place even while the compiler is still assembling its mutable draft.
    """

    groups = output.get("proxy-groups", [])
    if not isinstance(groups, list):
        return {"groups": []}
    graph = RuntimeGraph.from_candidate(output)
    exposed: list[str] = []

    for public in groups:
        if not isinstance(public, dict) or public.get("hidden", False):
            continue
        name = str(public.get("name", ""))
        if name in excluded_groups:
            continue
        references = public.get("proxies", [])
        if not isinstance(references, list) or len(references) != 1:
            continue
        provider_names = list(graph.provider_order(str(references[0])))
        if provider_names:
            public["use"] = provider_names
            exposed.append(name)

    return {"groups": sorted(exposed)}


def compile_runtime_graph(
    *,
    root: Path,
    config: dict[str, Any],
    policies: dict[str, Any],
    nodes: list[Node],
    known_source_ids: set[str],
    external_rule_providers: dict[str, Any] | None = None,
    external_rules: list[dict[str, Any]] | None = None,
    acl_groups: list[dict[str, Any]] | None = None,
    final_target: str | None = None,
    final_excluded_sources: list[str] | None = None,
) -> CompiledRuntime:
    """Compile the complete pre-qualification runtime topology.

    The low-level generator creates only the compiler's private mutable draft.
    All routing/group/source/browsing transformations are completed here before
    the final RuntimeGraph crosses the compiler boundary.
    """

    group_specs = list(acl_groups or [])
    excluded_group_names = {str(item["display_name"]) for item in group_specs}

    output, base_report = generate_config(
        root=root,
        config=config,
        policies=policies,
        nodes=nodes,
        external_rule_providers=external_rule_providers,
        external_rules=external_rules,
        external_groups=group_specs,
        final_target=final_target,
    )

    group_semantics = (
        apply_acl4ssr_group_semantics(
            output,
            group_specs=group_specs,
            pool_specs=list(policies["pools"]),
        )
        if group_specs
        else {}
    )
    source_exclusions = apply_acl4ssr_source_exclusions(
        output,
        group_specs=group_specs,
        known_source_ids=known_source_ids,
        rule_specs=external_rules,
        final_target=final_target,
        final_excluded_sources=list(final_excluded_sources or []),
    )
    manual_exposure = _expose_manual_provider_choices(output, excluded_groups=excluded_group_names)
    browsing_runtime = (
        harden_browsing_runtime(output, policies) if group_specs else {"status": "not_applicable"}
    )
    validate_browsing_public_surface(output)

    report: dict[str, Any] = dict(base_report)
    if group_semantics:
        report["acl4ssr_groups"] = group_semantics
    if source_exclusions:
        report["source_exclusions"] = source_exclusions
    if manual_exposure["groups"]:
        report["manual_provider_exposure"] = manual_exposure
    if browsing_runtime.get("status") != "not_applicable":
        report["browsing_runtime"] = browsing_runtime

    return CompiledRuntime(graph=RuntimeGraph.from_candidate(output), report=report)
