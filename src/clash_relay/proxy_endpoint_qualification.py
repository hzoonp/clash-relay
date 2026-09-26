"""Pre-publish TCP endpoint admission with aggregate-only diagnostics.

Authority boundary: this stage only filters obviously dead TCP endpoints from
a GitHub Runner vantage point. It is never evidence of China Telecom /
Unicom / Mobile quality. A timeout remains reserve evidence, while DNS failure is inconclusive.
Endpoints that succeed on every attempt carry robust evidence; partial success
remains reserve evidence. Endpoint reserve nodes are capped out of preferred
runtime pools after service qualification. UDP-native transports remain owned by the Mihomo
runtime probes.
"""

from __future__ import annotations

import copy
import errno
import hashlib
import ipaddress
import re
import socket
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .classify import proxy_fingerprint
from .errors import ValidationError
from .runtime_names import parse_runtime_source_name

_TCP_TYPES = frozenset(
    {"ss", "ssr", "vmess", "vless", "trojan", "http", "socks5", "snell", "anytls", "ssh", "mieru"}
)
_UDP_NATIVE_TYPES = frozenset({"hysteria", "hysteria2", "tuic", "wireguard", "masque"})
_REGIONS = frozenset({"hk", "tw", "sg", "jp", "us", "kr", "other"})
_ATTEMPTS = 3
_ADMISSION_QUORUM = 1
_CONNECT_TIMEOUT = 1.5
_ATTEMPT_BUDGET_SECONDS = 2.0
_UNREACHABLE_ERRNOS = frozenset(
    value
    for value in (
        getattr(errno, name, None)
        for name in (
            "EHOSTUNREACH",
            "ENETUNREACH",
            "ENETDOWN",
            "ENETRESET",
            "WSAEHOSTUNREACH",
            "WSAENETUNREACH",
            "WSAENETDOWN",
            "WSAENETRESET",
        )
    )
    if value is not None
)


def _public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_global


def _failure_category(error: OSError) -> str:
    if isinstance(error, (socket.timeout, TimeoutError)):
        return "connect_timeout"
    if isinstance(error, ConnectionRefusedError):
        return "connection_refused"
    if error.errno in _UNREACHABLE_ERRNOS:
        return "network_unreachable"
    return "connect_failure"


def _probe_tcp(server: str, port: int) -> tuple[bool, str, int]:
    """Bounded-retry TCP probe: (admitted, last_failure_category, successes).

    ``successes`` counts *attempts* in which at least one resolved address
    connected — a multi-address hostname never turns one attempt into several
    successes. Every attempt runs so robustness (success on every attempt) is
    observable even for endpoints that never fail. Do not send application
    data or log the target.
    """

    try:
        addresses = socket.getaddrinfo(server, port, type=socket.SOCK_STREAM)
    except OSError:
        return False, "dns_failure", 0
    public: list[tuple[socket.AddressFamily, tuple]] = []
    seen_addresses: set[tuple[socket.AddressFamily, tuple]] = set()
    for family, _, _, _, sockaddr in addresses:
        if not _public_ip(str(sockaddr[0])):
            continue
        marker = (family, sockaddr)
        if marker in seen_addresses:
            continue
        seen_addresses.add(marker)
        public.append(marker)
    if not public:
        return False, "non_public_address", 0
    successes = 0
    category = "connect_failure"
    for _ in range(_ATTEMPTS):
        attempt_succeeded = False
        # One attempt carries a total time budget: many resolved addresses
        # must never multiply into attempts * timeout of runner time.
        deadline = time.monotonic() + _ATTEMPT_BUDGET_SECONDS
        for family, sockaddr in public:
            if time.monotonic() >= deadline:
                break
            try:
                with socket.socket(family, socket.SOCK_STREAM) as connection:
                    connection.settimeout(_CONNECT_TIMEOUT)
                    connection.connect(sockaddr)
                attempt_succeeded = True
                break  # one reachable address ends this attempt
            except OSError as exc:
                category = _failure_category(exc)
        if attempt_succeeded:
            successes += 1
    admitted = successes >= _ADMISSION_QUORUM
    return admitted, ("answered" if admitted else category), successes


def _admission_tier(successes: int) -> str:
    if successes >= _ATTEMPTS:
        return "robust"
    if successes >= _ADMISSION_QUORUM:
        return "reserve"
    return "quarantined"


def _source(name: object) -> str:
    source = parse_runtime_source_name(name)
    if source is None:
        raise ValidationError("endpoint qualification found an invalid runtime source name")
    return source


def _region(provider_name: str) -> str:
    suffix = provider_name.rsplit("_", 1)[-1].lower()
    return suffix if suffix in _REGIONS else "other"


def quarantine_unreachable_tcp_endpoints(
    config: dict[str, Any], *, workers: int = 12, reserve_names: set[str] | None = None
) -> dict[str, Any]:
    """Prune failed TCP entries while keeping UDP-native transports for Mihomo probes."""
    providers = config.get("proxy-providers")
    if not isinstance(providers, dict):
        providers = {}
    endpoints: set[tuple[str, int]] = set()
    for provider in providers.values():
        payload = provider.get("payload") if isinstance(provider, dict) else None
        if not isinstance(payload, list):
            continue
        for proxy in payload:
            if not isinstance(proxy, dict) or proxy.get("type") not in _TCP_TYPES:
                continue
            server, port = proxy.get("server"), proxy.get("port")
            if isinstance(server, str) and isinstance(port, int) and not isinstance(port, bool):
                endpoints.add((server, port))
    if not endpoints:
        skipped_udp = sum(
            1
            for provider in providers.values()
            for proxy in (provider.get("payload", []) if isinstance(provider, dict) else [])
            if isinstance(proxy, dict) and proxy.get("type") in _UDP_NATIVE_TYPES
        )
        return {
            "status": "skipped",
            "tcp_nodes": 0,
            "tested": 0,
            "reachable": 0,
            "unreachable": 0,
            "quarantined": 0,
            "skipped_udp_native": skipped_udp,
            "attempts": _ATTEMPTS,
            "admission_quorum": _ADMISSION_QUORUM,
            "robust_endpoints": 0,
            "reserve_endpoints": 0,
            "dns_inconclusive": 0,
            "timeout_reserve": 0,
            "unique_quarantined_nodes": 0,
            "unique_quarantined_endpoints": 0,
            "by_source": {},
            "by_region": {},
            "by_protocol": {},
            "by_failure_category": {},
        }
    if workers < 1:
        raise ValidationError("endpoint qualification requires positive workers")
    ordered = sorted(endpoints)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = dict(
            zip(ordered, executor.map(lambda endpoint: _probe_tcp(*endpoint), ordered), strict=True)
        )
    counts: Counter[str] = Counter()
    tiers: Counter[str] = Counter()
    unique_quarantined: set[tuple[str, str]] = set()
    quarantined_endpoints: set[tuple[str, str]] = set()
    by_source: Counter[str] = Counter()
    by_region: Counter[str] = Counter()
    by_protocol: Counter[str] = Counter()
    by_failure_category: Counter[str] = Counter()
    by_source_failure_category: dict[str, Counter[str]] = {}
    replacements: dict[str, list[dict[str, Any]]] = {}
    for provider_name, provider in providers.items():
        payload = provider.get("payload") if isinstance(provider, dict) else None
        if not isinstance(payload, list):
            continue
        kept: list[dict[str, Any]] = []
        for proxy in payload:
            if not isinstance(proxy, dict):
                continue
            kind = proxy.get("type")
            if kind in _UDP_NATIVE_TYPES:
                counts["skipped_udp_native"] += 1
                kept.append(proxy)
                continue
            if kind not in _TCP_TYPES:
                kept.append(proxy)
                continue
            counts["tcp_nodes"] += 1
            counts["tested"] += 1
            server, port = proxy.get("server"), proxy.get("port")
            key = (server, port) if isinstance(server, str) and isinstance(port, int) else None
            admitted, failure_category, successes = (
                results[key]
                if key is not None and key in results
                else (False, "connect_failure", 0)
            )
            if not admitted and failure_category == "dns_failure":
                # Stage 1 already DNS-qualified this hostname through the
                # candidate's own DoH resolvers; the runner's system DNS
                # failing here is inconclusive, never node evidence.
                counts["dns_inconclusive"] += 1
                kept.append(proxy)
                continue
            if not admitted and failure_category == "connect_timeout":
                counts["timeout_reserve"] += 1
                tiers["reserve"] += 1
                if reserve_names is not None:
                    reserve_names.add(str(proxy["name"]))
                kept.append(proxy)
                continue
            if admitted:
                counts["reachable"] += 1
                tiers[_admission_tier(successes)] += 1
                if successes < _ATTEMPTS and reserve_names is not None:
                    reserve_names.add(str(proxy["name"]))
                kept.append(proxy)
                continue
            counts["unreachable"] += 1
            counts["quarantined"] += 1
            tiers["quarantined"] += 1
            source = _source(proxy.get("name"))
            unique_quarantined.add((source, proxy_fingerprint(proxy)))
            quarantined_endpoints.add((str(server), str(port)))
            by_source[source] += 1
            by_region[_region(str(provider_name))] += 1
            by_protocol[str(kind)] += 1
            by_failure_category[failure_category] += 1
            by_source_failure_category.setdefault(source, Counter())[failure_category] += 1
        if payload and not kept:
            raise ValidationError("endpoint qualification would empty a proxy provider")
        replacements[str(provider_name)] = kept
    for provider_name, kept in replacements.items():
        provider = providers[provider_name]
        if isinstance(provider, dict):
            provider["payload"] = kept
    return {
        "status": "passed",
        **{
            key: counts[key]
            for key in (
                "tcp_nodes",
                "tested",
                "reachable",
                "unreachable",
                "quarantined",
                "skipped_udp_native",
            )
        },
        "attempts": _ATTEMPTS,
        "admission_quorum": _ADMISSION_QUORUM,
        "robust_endpoints": int(tiers["robust"]),
        "reserve_endpoints": int(tiers["reserve"]),
        "dns_inconclusive": int(counts["dns_inconclusive"]),
        "timeout_reserve": int(counts["timeout_reserve"]),
        "unique_quarantined_nodes": len(unique_quarantined),
        "unique_quarantined_endpoints": len(quarantined_endpoints),
        "by_source": dict(sorted(by_source.items())),
        "by_region": dict(sorted(by_region.items())),
        "by_protocol": dict(sorted(by_protocol.items())),
        "by_failure_category": dict(sorted(by_failure_category.items())),
        "by_source_failure_category": {
            source: dict(sorted(categories.items()))
            for source, categories in sorted(by_source_failure_category.items())
        },
    }


def endpoint_tier_group_name(parent: str, tier: str) -> str:
    return f"__CR_ENDPOINT_{hashlib.sha256(parent.encode('utf-8')).hexdigest()[:16]}_{tier}"


def cap_endpoint_reserve_pools(config: dict[str, Any], reserve_names: set[str]) -> int:
    """Keep private endpoint evidence out of preferred pools, with explicit failover.

    Existing service qualification filters remain authoritative. Empty preferred
    browsing groups explicitly reject, preventing Mihomo's empty-group DIRECT
    fallback. Names stay in the private configuration, never aggregate reports.
    """
    if not reserve_names:
        return 0
    providers = config.get("proxy-providers", {})
    groups = config.get("proxy-groups", [])
    by_name = {group.get("name"): group for group in groups if isinstance(group, dict)}
    additions = []
    changed = 0

    def exact(names: set[str]) -> str:
        # RE2-compatible literals; Python's re.escape also escapes whitespace.
        meta = frozenset("\\.+*?()|[]{}^$")
        return (
            "^("
            + "|".join(
                "".join("\\" + char if char in meta else char for char in name)
                for name in sorted(names)
            )
            + ")$"
            if names
            else "^$"
        )

    for group in list(groups):
        if not isinstance(group, dict) or group.get("type") != "url-test":
            continue
        uses = group.get("use", [])
        if not uses or not all(
            str(key).startswith(("cr_general_", "cr_browsing_")) for key in uses
        ):
            continue
        name = str(group.get("name", ""))
        if name.startswith("__CR_ENDPOINT_"):
            continue
        browsing = any(str(key).startswith("cr_browsing_") for key in uses)
        if browsing and not name.endswith("_STABLE_AUTO"):
            continue
        names = {
            str(proxy["name"])
            for key in uses
            for proxy in providers.get(key, {}).get("payload", [])
            if isinstance(proxy, dict) and isinstance(proxy.get("name"), str)
        }
        pattern = group.get("filter")
        excluded = group.get("exclude-filter")
        allowed = {
            item
            for item in names
            if (not pattern or re.search(pattern, item))
            and (not excluded or not re.search(excluded, item))
        }
        reserve = allowed & reserve_names
        if not reserve:
            continue
        preferred = allowed - reserve
        if browsing:
            group["filter"] = exact(preferred)
            if not preferred:
                group["proxies"] = ["REJECT"]
            reserve_group = by_name.get(name.replace("_STABLE_AUTO", "_RESERVE_AUTO"))
            if isinstance(reserve_group, dict):
                old_filter = reserve_group.get("filter")
                old_names = {
                    item for item in names if not old_filter or re.search(old_filter, item)
                }
                reserve_group["filter"] = exact(old_names | reserve)
        else:
            children = []
            for tier, members in (("ROBUST", preferred), ("RESERVE", reserve)):
                if not members:
                    continue
                child = copy.deepcopy(group)
                child_name = endpoint_tier_group_name(name, tier)
                if child_name in by_name:
                    raise ValidationError("endpoint tier group name collision")
                child.update(name=child_name, hidden=True, filter=exact(members))
                child.pop("proxies", None)
                additions.append(child)
                children.append(child_name)
            for key in (
                "use",
                "filter",
                "exclude-filter",
                "tolerance",
                "include-all",
                "include-all-providers",
                "include-all-proxies",
            ):
                group.pop(key, None)
            group.update(type="fallback", proxies=children)
        changed += 1
    groups.extend(additions)
    return changed


def accelerate_client_health_checks(config: dict[str, Any]) -> int:
    """Switch browsing groups after one failed probe; keep other non-AI at two."""
    groups = config.get("proxy-groups")
    if not isinstance(groups, list):
        return 0
    changed = 0
    for group in groups:
        if not isinstance(group, dict) or group.get("type") not in {"url-test", "fallback"}:
            continue
        name = group.get("name")
        if not isinstance(name, str) or name.startswith(("AI ·", "__CR_AI_")):
            continue
        if not isinstance(group.get("url"), str):
            continue
        uses = group.get("use")
        browsing = (
            name == "网页自动"
            or name.startswith(("网页 · ", "__CR_BROWSING_"))
            or (
                isinstance(uses, list)
                and any(str(provider).startswith("cr_browsing_") for provider in uses)
            )
        )
        group["max-failed-times"] = 1 if browsing else 2
        changed += 1
    return changed
