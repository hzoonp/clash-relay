"""Subscription-scoped node admission policies."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

_MULTIPLIER_PATTERNS = (
    re.compile(r"(?<![0-9.])(\d+(?:\.\d+)?)\s*(?:[xX\u00d7]|倍)(?![A-Za-z0-9.])"),
    re.compile(
        r"(?:倍率|倍数)\s*[:\uff1a=]?\s*(\d+(?:\.\d+)?)(?:\s*[xX\u00d7倍])?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![A-Za-z0-9])[xX\u00d7]\s*(?!(?:32|64|86)(?:\b|_))(\d+(?:\.\d+)?)(?![0-9.])",
    ),
)

_INFORMATIONAL_NODE_PATTERNS = (
    re.compile(
        r"(?:剩余流量|流量剩余|套餐到期|距离到期|距离重置|下次重置|官网地址|官方网站|联系客服|在线客服|订阅到期|过期时间)"
    ),
    re.compile(r"(?:^|[\s|\uff5c])剩余\s*[:\uff1a]"),
    re.compile(r"\b(?:traffic|expire|remaining|reset)\s*[:\uff1a]", re.IGNORECASE),
)


def filter_informational_proxies(
    proxies: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Drop high-confidence subscription status or support pseudo-nodes.

    The filter is intentionally conservative. Generic words such as "流量",
    "套餐", "官网", or "节点" are not sufficient by themselves because real
    endpoint names may legitimately contain them.
    """

    kept: list[dict[str, Any]] = []
    rejected = 0
    for proxy in proxies:
        name = str(proxy.get("name", ""))
        if any(pattern.search(name) is not None for pattern in _INFORMATIONAL_NODE_PATTERNS):
            rejected += 1
            continue
        kept.append(proxy)
    return kept, rejected


def node_name_multiplier(name: str) -> float | None:
    """Return the highest explicit multiplier marker found in a node name.

    Architecture tokens such as x64 and X86_64 are intentionally not treated as
    billing multipliers. Ambiguous names without an explicit multiplier marker
    remain admitted rather than being rejected by heuristic inference.
    """

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


def filter_proxies_by_name_patterns(
    proxies: Iterable[dict[str, Any]], *, deny_patterns: Iterable[str]
) -> tuple[list[dict[str, Any]], int]:
    """Drop proxies whose names match an explicit subscription admission deny-list."""

    rows = list(proxies)
    compiled = tuple(re.compile(pattern) for pattern in deny_patterns)
    if not compiled:
        return rows, 0

    kept: list[dict[str, Any]] = []
    rejected = 0
    for proxy in rows:
        name = str(proxy.get("name", ""))
        if any(pattern.search(name) is not None for pattern in compiled):
            rejected += 1
            continue
        kept.append(proxy)
    return kept, rejected


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
