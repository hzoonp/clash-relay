"""Subscription-scoped node admission policies."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

_MULTIPLIER_PATTERNS = (
    re.compile(r"(?<![0-9.])(\d+(?:\.\d+)?)\s*(?:[xX×]|倍)(?![A-Za-z0-9.])"),
    re.compile(r"(?:倍率|倍数)\s*[:：=]?\s*(\d+(?:\.\d+)?)(?:\s*[xX×倍])?", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])[xX×]\s*(\d+(?:\.\d+)?)(?![0-9.])"),
)


def node_name_multiplier(name: str) -> float | None:
    """Return the highest explicit multiplier marker found in a node name."""

    values: list[float] = []
    for pattern in _MULTIPLIER_PATTERNS:
        for match in pattern.finditer(name):
            try:
                value = float(match.group(1))
            except (TypeError, ValueError):
                continue
            if value > 0:
                values.append(value)
    return max(values) if values else None


def filter_proxies_by_multiplier(
    proxies: Iterable[dict[str, Any]], *, max_multiplier: float | None
) -> tuple[list[dict[str, Any]], int]:
    """Drop proxies whose explicit name multiplier is above the configured ceiling."""

    rows = list(proxies)
    if max_multiplier is None:
        return rows, 0

    kept: list[dict[str, Any]] = []
    rejected = 0
    for proxy in rows:
        multiplier = node_name_multiplier(str(proxy.get("name", "")))
        if multiplier is not None and multiplier > max_multiplier:
            rejected += 1
            continue
        kept.append(proxy)
    return kept, rejected
