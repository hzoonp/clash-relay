"""Private-config inventory: aggregate estimates only; never sends probes."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from typing import Any


def health_check_inventory(config: dict[str, Any]) -> dict[str, Any]:
    """Estimate periodic check load with lazy checks active and filters ignored.

    Nested groups expand to all leaves, so the estimate intentionally overcounts
    groups that test only their selected member. Unknown external payloads and
    cyclic/missing references are reported, not silently assumed to be empty.
    Runtime retries, startup bursts and manual checks are outside this estimate.
    """
    providers = config.get("proxy-providers", {})
    groups = {item["name"]: item for item in config.get("proxy-groups", [])}
    proxies = {item["name"] for item in config.get("proxies", [])}
    occurrences: Counter[str] = Counter()
    checks = lazy = unknown = slots = 0
    hourly = 0.0

    def provider_size(name: str) -> tuple[int, bool]:
        provider = providers.get(name, {})
        payload = provider.get("payload")
        if not isinstance(payload, list):
            return 0, True
        return len(payload), False

    def group_size(name: str, seen: frozenset[str]) -> tuple[int, bool]:
        if name in {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE"}:
            return 0, False
        if name in proxies:
            return 1, False
        if name in seen or name not in groups:
            return 0, True
        group = groups[name]
        sizes = [provider_size(provider) for provider in group.get("use", [])]
        sizes.extend(group_size(child, seen | {name}) for child in group.get("proxies", []))
        if group.get("include-all") or group.get("include-all-providers"):
            sizes.extend(provider_size(provider) for provider in providers)
        if group.get("include-all") or group.get("include-all-proxies"):
            sizes.append((len(proxies), False))
        return sum(size for size, _ in sizes), any(missing for _, missing in sizes)

    def record(check: dict[str, Any], size: int, incomplete: bool) -> None:
        nonlocal checks, lazy, unknown, slots, hourly
        checks += 1
        lazy += bool(check.get("lazy", True))
        slots += size
        interval = check.get("interval")
        valid_interval = (
            isinstance(interval, (int, float))
            and not isinstance(interval, bool)
            and math.isfinite(interval)
            and interval > 0
        )
        unknown += bool(incomplete or not valid_interval)
        if (
            isinstance(interval, (int, float))
            and not isinstance(interval, bool)
            and math.isfinite(interval)
            and interval > 0
        ):
            hourly += size * 3600 / interval

    provider_checks = 0
    for name, provider in providers.items():
        check = provider.get("health-check", {})
        if not check.get("enable"):
            continue
        provider_checks += 1
        record(check, *provider_size(name))
        for proxy in provider.get("payload", []):
            identity = {key: value for key, value in proxy.items() if key != "name"}
            digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            occurrences[digest] += 1
    for name, group in groups.items():
        if group.get("type") in {"url-test", "fallback", "load-balance"} and group.get("url"):
            record(group, *group_size(name, frozenset()))

    return {
        "schema_version": 1,
        "measurement_kind": "static_inventory",
        "provider_checks": provider_checks,
        "group_checks": checks - provider_checks,
        "lazy_checks": lazy,
        "expanded_target_slots": slots,
        "unique_checked_provider_nodes": len(occurrences),
        "repeated_provider_node_occurrences": sum(occurrences.values()) - len(occurrences),
        "estimated_periodic_requests_per_hour": round(hourly, 2),
        "checks_with_incomplete_estimate": unknown,
        "assumptions": [
            "lazy_checks_active",
            "filters_ignored",
            "nested_groups_expanded_to_all_leaves",
            "unknown_payloads_and_intervals_excluded_from_rate",
            "startup_manual_checks_and_retries_excluded",
            "not_a_network_measurement",
        ],
    }
