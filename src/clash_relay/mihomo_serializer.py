"""Serialize a compiled RuntimeGraph into an isolated Mihomo document."""

from __future__ import annotations

import copy
from typing import Any

from .runtime_graph import RuntimeGraph


def serialize_runtime_graph(graph: RuntimeGraph) -> dict[str, Any]:
    """Return a detached Mihomo document from the final compiled graph.

    The deep copy makes the compiler/serializer boundary explicit: validation,
    YAML rendering, and later qualification stages cannot mutate the compiled
    graph by aliasing its internal candidate mapping.
    """

    return copy.deepcopy(dict(graph.candidate))
