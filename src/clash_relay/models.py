"""Immutable internal models used between pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SubscriptionSpec:
    id: str
    display_name: str
    enabled: bool
    required: bool
    secret_name: str
    priority: int
    on_error: str
    allowed_uses: frozenset[str]
    allowed_countries: frozenset[str]
    default_capabilities: frozenset[str]
    default_cost_level: str
    max_node_multiplier: float | None = None
    deny_name_patterns: tuple[str, ...] = ()
    node_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    name_rules: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class NodeOccurrence:
    """Source-specific classification retained for one physical proxy identity."""

    source_id: str
    source_display_name: str
    source_priority: int
    source_allowed_uses: frozenset[str]
    source_allowed_countries: frozenset[str]
    original_name: str
    country: str
    capabilities: frozenset[str]
    cost_level: str


@dataclass(frozen=True, slots=True)
class Node:
    source_id: str
    source_display_name: str
    source_priority: int
    source_allowed_uses: frozenset[str]
    source_allowed_countries: frozenset[str]
    original_name: str
    proxy: dict[str, Any]
    country: str
    capabilities: frozenset[str]
    cost_level: str
    fingerprint: str
    occurrences: tuple[NodeOccurrence, ...] = ()


@dataclass(frozen=True, slots=True)
class BuildResult:
    config: dict[str, Any]
    yaml_text: str
    report: dict[str, Any]
    secret_values: tuple[str, ...]
