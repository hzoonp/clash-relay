"""Aggregate-only pre-publish admission for proxy server hostnames."""

from __future__ import annotations

import ipaddress
import json
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any

from .errors import ValidationError


def _public_address(value: object) -> bool:
    try:
        address = ipaddress.ip_address(str(value))
    except ValueError:
        return False
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
    )


def _resolve(endpoint: str, hostname: str, timeout: float = 3.0) -> tuple[bool, bool]:
    """Return (answered, transport_failed), without retaining DNS payloads."""
    try:
        query = urllib.parse.urlencode({"name": hostname, "type": "A"})
        request = urllib.request.Request(
            f"{endpoint}?{query}", headers={"Accept": "application/dns-json"}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except (OSError, ValueError, json.JSONDecodeError):
        return False, True
    answers = payload.get("Answer", []) if isinstance(payload, dict) else []
    return any(
        isinstance(row, dict) and row.get("type") in {1, 28} and _public_address(row.get("data"))
        for row in answers
    ), False


def _doh_endpoints(resolvers: list[Any]) -> list[str]:
    """Keep only runner-executable DoH JSON endpoints.

    The runner can probe HTTPS DoH endpoints only; a ``system`` entry names the
    OS resolver and other non-HTTPS entries are not DoH, so they cannot take
    part in this global preflight and are excluded instead of crashing the
    stage. Admission stays fail-closed through the two-endpoint minimum below.
    """

    endpoints: list[str] = []
    for resolver in resolvers:
        try:
            parsed = urllib.parse.urlsplit(str(resolver))
        except ValueError:
            continue
        if parsed.scheme == "https" and parsed.hostname:
            endpoints.append(str(resolver))
    return endpoints


def quarantine_unresolvable_proxy_hosts(config: dict[str, Any]) -> dict[str, Any]:
    """Remove only hostname proxies unanswered by every configured resolver."""
    providers = config.get("proxy-providers")
    if not isinstance(providers, dict):
        providers = {}
    hostname_inventory = any(
        isinstance(proxy, dict)
        and isinstance(proxy.get("server"), str)
        and not _public_address(proxy["server"])
        for provider in providers.values()
        if isinstance(provider, dict) and isinstance(provider.get("payload"), list)
        for proxy in provider["payload"]
    )
    if not hostname_inventory:
        return {
            "status": "skipped",
            "hostname_nodes": 0,
            "ip_literal_nodes": 0,
            "resolved": 0,
            "unresolved": 0,
            "resolver_disagreement": 0,
            "quarantined": 0,
            "by_source": {},
            "by_region": {},
            "by_protocol": {},
        }
    dns = config.get("dns")
    resolvers = dns.get("proxy-server-nameserver") if isinstance(dns, dict) else None
    if not isinstance(resolvers, list):
        raise ValidationError(
            "proxy hostname qualification requires proxy-server-nameserver resolvers"
        )
    endpoints = _doh_endpoints(resolvers)
    if len(endpoints) < 2:
        raise ValidationError(
            "proxy hostname qualification requires multiple DoH proxy-server-nameserver resolvers"
        )
    counts = Counter()
    dimensions: dict[str, Counter[str]] = {
        "source": Counter(),
        "region": Counter(),
        "protocol": Counter(),
    }
    cache: dict[str, tuple[bool, bool]] = {}
    for provider_name, provider in providers.items():
        if not isinstance(provider, dict) or not isinstance(provider.get("payload"), list):
            continue
        kept: list[dict[str, Any]] = []
        for proxy in provider["payload"]:
            if not isinstance(proxy, dict):
                continue
            server = proxy.get("server")
            if not isinstance(server, str):
                continue
            protocol = str(proxy.get("type", "unknown"))
            if _public_address(server):
                counts["ip_literal_nodes"] += 1
                kept.append(proxy)
                continue
            counts["hostname_nodes"] += 1
            if server not in cache:
                results = [_resolve(endpoint, server) for endpoint in endpoints]
                cache[server] = (
                    any(answered for answered, _ in results),
                    any(answered for answered, _ in results)
                    and any(failed for _, failed in results),
                )
            resolved, disagreement = cache[server]
            if disagreement:
                counts["resolver_disagreement"] += 1
            if resolved:
                counts["resolved"] += 1
                kept.append(proxy)
                continue
            counts["unresolved"] += 1
            counts["quarantined"] += 1
            dimensions["source"][str(provider_name)] += 1
            dimensions["region"][str(provider_name).rsplit("_", 1)[-1]] += 1
            dimensions["protocol"][protocol] += 1
        if not kept:
            raise ValidationError("proxy hostname qualification would empty a provider")
        provider["payload"] = kept
    return {
        "status": "passed",
        **{
            key: int(counts[key])
            for key in (
                "hostname_nodes",
                "ip_literal_nodes",
                "resolved",
                "unresolved",
                "resolver_disagreement",
                "quarantined",
            )
        },
        "by_source": dict(sorted(dimensions["source"].items())),
        "by_region": dict(sorted(dimensions["region"].items())),
        "by_protocol": dict(sorted(dimensions["protocol"].items())),
    }
