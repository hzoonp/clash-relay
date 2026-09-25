"""Aggregate systemic probe-environment detection for OpenAI live qualification.

A systemic failure means the *probe environment* (or the OpenAI upstream) is
unreachable for every candidate at once — runner-side blocking, an upstream
outage — which must never be recorded as per-node failures. Detection is
aggregate-only: it consumes counts, outcome categories, and region coverage,
never node identities, hostnames, or subscription information.

Two evidence mechanisms guard OpenAI qualification:

1. A bounded deterministic sentinel probe runs before the full sweep. A small
   sentinel set spanning distinct regions is probed against the OpenAI
   critical endpoints plus a connectivity control; if every sentinel fails
   OpenAI while the control succeeds through the same sentinels, the probe
   environment is blocked and the full sweep is skipped.
2. A post-probe detector re-checks the full sweep with the same aggregate
   rule, so a mid-run environment collapse cannot be recorded as mass node
   failure.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

SENTINEL_COUNT = 3
_MIN_SYSTEMIC_SAMPLE = 2
_MIN_SYSTEMIC_REGIONS = 2
_DOMINANT_OUTCOME_RATIO = 0.8
_NETWORK_OUTCOMES = frozenset(
    {"timeout", "tls_error", "dns_error", "connection_error", "network_error"}
)


def extract_region(runtime_name: str) -> str:
    """Return the aggregate region label of an AI runtime name.

    AI runtime names carry a scope token such as ``[AI:JP]``; the region is
    the aggregate-safe label after the colon. Names without a scope token
    project to ``other``.
    """

    name = str(runtime_name)
    if name.startswith("[AI:") and "]" in name:
        region = name[4 : name.index("]")].strip().lower()
        return region or "other"
    return "other"


def select_sentinels(live_names: Iterable[str], *, count: int = SENTINEL_COUNT) -> tuple[str, ...]:
    """Deterministically pick a bounded sentinel set spanning distinct regions.

    Names are sorted and assigned round-robin over sorted region labels, so
    every region contributes its alphabetically first node before any region
    contributes a second. Selection is pure: identical inputs always produce
    identical sentinels.
    """

    by_region: dict[str, list[str]] = {}
    for name in sorted(str(name) for name in live_names):
        by_region.setdefault(extract_region(name), []).append(name)
    sentinels: list[str] = []
    index = 0
    while len(sentinels) < count:
        progressed = False
        for region in sorted(by_region):
            names = by_region[region]
            if index < len(names):
                sentinels.append(names[index])
                progressed = True
                if len(sentinels) >= count:
                    break
        if not progressed:
            break
        index += 1
    return tuple(sentinels)


def _dominant_outcome(outcome_counts: Mapping[str, int]) -> str | None:
    if not outcome_counts:
        return None
    total = sum(int(count) for count in outcome_counts.values())
    if total <= 0:
        return None
    network_total = sum(
        int(count) for outcome, count in outcome_counts.items() if outcome in _NETWORK_OUTCOMES
    )
    if network_total / total < _DOMINANT_OUTCOME_RATIO:
        return None
    return min(
        (outcome for outcome, count in outcome_counts.items() if outcome in _NETWORK_OUTCOMES),
        key=lambda outcome: (-int(outcome_counts[outcome]), outcome),
    )


def detect_systemic_failure(
    *,
    live_tested: int,
    qualified_nodes: int,
    tested_regions: int,
    failed_regions: int,
    outcome_counts: Mapping[str, int],
    control_passed: bool,
) -> tuple[bool, str | None]:
    """Decide whether OpenAI live failures are systemic (environment-level).

    All aggregate conditions must hold: no node qualified, a meaningful sample
    spanning multiple regions failed, failures are dominated by network-level
    outcomes (rather than HTTP rejections that prove the probe reached
    OpenAI), and the connectivity control passed through the same probe
    environment. The returned category is the dominant network outcome.
    """

    dominant = _dominant_outcome(outcome_counts)
    if not control_passed or dominant is None:
        return False, None
    if live_tested < _MIN_SYSTEMIC_SAMPLE or qualified_nodes > 0:
        return False, None
    if tested_regions < _MIN_SYSTEMIC_REGIONS or failed_regions < _MIN_SYSTEMIC_REGIONS:
        return False, None
    return True, dominant
