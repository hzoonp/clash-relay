"""Pre-publish TCP endpoint admission with aggregate-only diagnostics."""

from __future__ import annotations

import ipaddress
import re
import socket
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
_ATTEMPTS = 2
_CONNECT_TIMEOUT = 1.5


def _public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_global


def _probe_tcp(server: str, port: int) -> bool:
    """Test a public address twice; do not send application data or log the target."""
    try:
        addresses = socket.getaddrinfo(server, port, type=socket.SOCK_STREAM)
    except OSError:
        return False
    public = [
        (family, sockaddr)
        for family, _, _, _, sockaddr in addresses
        if _public_ip(str(sockaddr[0]))
    ]
    if not public:
        return False
    for _ in range(_ATTEMPTS):
        for family, sockaddr in public:
            try:
                with socket.socket(family, socket.SOCK_STREAM) as connection:
                    connection.settimeout(_CONNECT_TIMEOUT)
                    connection.connect(sockaddr)
                return True
            except OSError:
                continue
    return False


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
            "by_source": {},
            "by_region": {},
            "by_protocol": {},
        }
    if workers < 1:
        raise ValidationError("endpoint qualification requires positive workers")
    ordered = sorted(endpoints)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = dict(
            zip(ordered, executor.map(lambda endpoint: _probe_tcp(*endpoint), ordered), strict=True)
        )
    counts: Counter[str] = Counter()
    by_source: Counter[str] = Counter()
    by_region: Counter[str] = Counter()
    by_protocol: Counter[str] = Counter()
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
            if key is not None and results.get(key, False):
                counts["reachable"] += 1
                kept.append(proxy)
                continue
            counts["unreachable"] += 1
            counts["quarantined"] += 1
            by_source[_source(proxy.get("name"))] += 1
            by_region[_region(str(provider_name))] += 1
            by_protocol[str(kind)] += 1
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
        "by_source": dict(sorted(by_source.items())),
        "by_region": dict(sorted(by_region.items())),
        "by_protocol": dict(sorted(by_protocol.items())),
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
