"""Pre-publish TCP endpoint admission with aggregate-only diagnostics.

Authority boundary: this stage only filters obviously dead TCP endpoints from
a GitHub Runner vantage point. It is never evidence of China Telecom /
Unicom / Mobile quality. A transient timeout never permanently quarantines an
endpoint on its own: every endpoint gets ``_ATTEMPTS`` bounded attempts and is
quarantined only when none of them succeeds (0-of-N quorum). Endpoints that
succeed on every attempt carry robust evidence; endpoints admitted with some
failed attempts carry reserve evidence and rely on the client runtime URLTest
for continuous re-selection. UDP-native transports remain owned by the Mihomo
runtime probes.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .errors import ValidationError

_TCP_TYPES = frozenset(
    {"ss", "ssr", "vmess", "vless", "trojan", "http", "socks5", "snell", "anytls", "ssh", "mieru"}
)
_UDP_NATIVE_TYPES = frozenset({"hysteria", "hysteria2", "tuic", "wireguard", "masque"})
_SOURCE_NAME = re.compile(r"\b(sub_[1-5])/", re.ASCII)
_REGIONS = frozenset({"hk", "tw", "sg", "jp", "us", "kr", "other"})
_ATTEMPTS = 3
_ADMISSION_QUORUM = 1
_CONNECT_TIMEOUT = 1.5
_ATTEMPT_BUDGET_SECONDS = 2.0
_UNREACHABLE_ERRNOS = frozenset(
    value
    for value in (
        getattr(socket, name, None)
        for name in ("EHOSTUNREACH", "ENETUNREACH", "ENETDOWN", "ENETRESET")
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
        return "refused"
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
        return False, "dns_failure", 0
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
    match = _SOURCE_NAME.search(name) if isinstance(name, str) else None
    return match.group(1) if match else "other"


def _region(provider_name: str) -> str:
    suffix = provider_name.rsplit("_", 1)[-1].lower()
    return suffix if suffix in _REGIONS else "other"


def quarantine_unreachable_tcp_endpoints(
    config: dict[str, Any], *, workers: int = 12
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
            "unique_quarantined_nodes": 0,
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
    unique_quarantined: dict[tuple[str, str, str, str], tuple[str, str]] = {}
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
                tiers["reserve"] += 1
                kept.append(proxy)
                continue
            if admitted:
                counts["reachable"] += 1
                tiers[_admission_tier(successes)] += 1
                kept.append(proxy)
                continue
            counts["unreachable"] += 1
            counts["quarantined"] += 1
            tiers["quarantined"] += 1
            source = _source(proxy.get("name"))
            unique_key = (
                source,
                str(server),
                str(port),
                str(kind),
            )
            unique_quarantined.setdefault(
                unique_key, (_region(str(provider_name)), failure_category)
            )
        if payload and not kept:
            raise ValidationError("endpoint qualification would empty a proxy provider")
        replacements[str(provider_name)] = kept
    for provider_name, kept in replacements.items():
        provider = providers[provider_name]
        if isinstance(provider, dict):
            provider["payload"] = kept
    by_source: Counter[str] = Counter(key[0] for key in unique_quarantined)
    by_region: Counter[str] = Counter(region for region, _ in unique_quarantined.values())
    by_protocol: Counter[str] = Counter(key[3] for key in unique_quarantined)
    by_failure_category: Counter[str] = Counter(
        category for _, category in unique_quarantined.values()
    )
    by_source_failure_category: dict[str, Counter[str]] = {}
    for unique_key, (_region_name, category) in unique_quarantined.items():
        by_source_failure_category.setdefault(unique_key[0], Counter())[category] += 1
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
        "unique_quarantined_nodes": len(unique_quarantined),
        "by_source": dict(sorted(by_source.items())),
        "by_region": dict(sorted(by_region.items())),
        "by_protocol": dict(sorted(by_protocol.items())),
        "by_failure_category": dict(sorted(by_failure_category.items())),
        "by_source_failure_category": {
            source: dict(sorted(categories.items()))
            for source, categories in sorted(by_source_failure_category.items())
        },
    }


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
