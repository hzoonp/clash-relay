"""Serialize a compiled RuntimeGraph into an isolated Mihomo document."""

from __future__ import annotations

from typing import Any

from .runtime_graph import RuntimeGraph


def serialize_runtime_graph(graph: RuntimeGraph) -> dict[str, Any]:
    """Return a detached Mihomo document from the final compiled graph.

    ``RuntimeGraph.candidate`` already returns a deep-copied snapshot, so that
    single copy is the compiler/serializer boundary: validation, YAML rendering,
    and later qualification stages cannot mutate the compiled graph by aliasing
    its internal candidate mapping.
    """

    return graph.candidate
