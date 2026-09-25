"""Aggregate systemic probe-environment detection for OpenAI live qualification.

A systemic failure means the *probe environment* (or the OpenAI upstream) is
unreachable for every candidate at once -- runner-side blocking, an upstream
outage -- which must never be recorded as per-node failures. Detection is
aggregate-only: it consumes counts, outcome categories, and region coverage
derived from canonical provider metadata, never node identities, hostnames,
or subscription information.

Two evidence mechanisms guard OpenAI qualification, and both evaluate the
exact same endpoint-blockage model:

1. A bounded deterministic sentinel sweep runs before the full sweep. One
   sentinel per candidate region is selected from the *complete* AI inventory
   (never from the post-cache live subset, so cache coverage cannot shrink
   region representation). Sentinels probe the OpenAI critical endpoints plus
   a connectivity control through the same nodes.
2. The full sweep's own per-endpoint results are re-checked with the same
   model, so a mid-run environment collapse cannot be recorded as mass node
   failure.

Sentinels are diagnostic-only: their outcomes never enter the qualification
cache.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

_STATUS_OUTCOME_PREFIX = "status_"
_MIN_BLOCKED_REGIONS = 2
_MIN_CONTROL_REGIONS = 2
_DOMINANT_OUTCOME_RATIO = 0.8


def select_sentinels(
    *,
    node_regions: Mapping[str, str],
) -> tuple[str, ...]:
    """Deterministically select one sentinel per distinct candidate region.

    ``node_regions`` maps every AI runtime node in the *full current
    candidate inventory* to its canonical region. Names are sorted and the
    alphabetically first node of each region is chosen, so selection is pure
    and bounded by the region count -- independent of cache coverage.
    """

    by_region: dict[str, list[str]] = {}
    for name in sorted(node_regions):
        by_region.setdefault(node_regions[name], []).append(name)
    return tuple(names[0] for _, names in sorted(by_region.items()))


def _is_network_outcome(outcome: str) -> bool:
    return not str(outcome).startswith(_STATUS_OUTCOME_PREFIX)


def evaluate_endpoint_blockage(
    *,
    critical_endpoints: Sequence[str],
    region_endpoint_stats: Mapping[str, Mapping[str, Mapping[str, Any]]],
    control_ok_regions: Mapping[str, str] | set[str],
) -> dict[str, Any]:
    """The single systemic evidence model used by sentinel and full sweep.

    ``region_endpoint_stats`` maps region -> endpoint -> aggregate counters
    (``probed`` plus per-category ``outcomes`` such as ``timeout`` or
    ``status_403``). ``control_ok_regions`` is the set of regions in which the
    connectivity control succeeded through the probed nodes.

    A critical endpoint is environment-blocked when probes from at least two
    regions all failed with network-level outcomes (the explicit ``reached``
    counter is zero -- an HTTP response proves the endpoint was reached) and
    every such failing region is covered by a successful connectivity control
    (the explicit ``passed`` counter of the control probe's own
    ``expected_status``).
    """

    blocked: list[str] = []
    dominant_counts: Counter[str] = Counter()
    for endpoint in critical_endpoints:
        failing_regions: list[str] = []
        reached = 0
        probed_regions = 0
        for region in sorted(region_endpoint_stats):
            stats = region_endpoint_stats[region].get(endpoint)
            if not isinstance(stats, Mapping) or int(stats.get("probed", 0)) < 1:
                continue
            probed_regions += 1
            reached += int(stats.get("reached", 0))
            if int(stats.get("network_failure", 0)) > 0:
                failing_regions.append(region)
        if reached > 0:
            continue  # at least one probe reached OpenAI: endpoint works
        if probed_regions < _MIN_BLOCKED_REGIONS:
            continue
        if len(failing_regions) < _MIN_BLOCKED_REGIONS:
            continue
        if not set(failing_regions) <= set(control_ok_regions):
            continue  # control coverage must span every verdict region
        blocked.append(endpoint)
    systemic = bool(blocked)
    if systemic:
        for endpoint in blocked:
            for region_stats_row in region_endpoint_stats.values():
                stats = region_stats_row.get(endpoint)
                if isinstance(stats, Mapping):
                    for outcome, count in (stats.get("outcomes") or {}).items():
                        if _is_network_outcome(str(outcome)):
                            dominant_counts[str(outcome)] += int(count)
    dominant = (
        min(dominant_counts, key=lambda outcome: (-dominant_counts[outcome], outcome))
        if dominant_counts
        else None
    )
    return {
        "systemic": systemic,
        "blocked_critical_endpoints": blocked,
        "dominant_failure_category": dominant,
    }
