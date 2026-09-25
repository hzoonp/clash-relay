"""Aggregate-only pre-publish admission for proxy server hostnames.

The runner probes each hostname through the candidate's DoH
``proxy-server-nameserver`` endpoints using RFC 8484 wireformat. Authority
boundary: this stage only filters hostnames that DNS proves unresolvable
(NXDOMAIN or a NOERROR response with no public A/AAAA) from a data-center
vantage point. It is never evidence of China Telecom / Unicom / Mobile
quality — a hostname the runner cannot resolve may still resolve fine on a
carrier access network. Runner transport failures never quarantine a node:
inconclusive probes are admitted as reserve-leaning evidence, and a stage in
which no resolver returned any DNS response fails closed instead of mass-
quarantining the inventory.
"""

from __future__ import annotations

import ipaddress
import re
import urllib.parse
from collections import Counter
from typing import Any

from .dns_wire import ANSWER_CATEGORIES, probe_doh
from .errors import ValidationError

_SOURCE_NAME = re.compile(r"\b(sub_[1-5])/", re.ASCII)
_REGIONS = frozenset({"hk", "tw", "sg", "jp", "us", "kr", "other"})
_DNS_CONFIRMED_CATEGORIES = frozenset({"nxdomain", "no_answer"})


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


def _doh_endpoints(resolvers: list[Any]) -> list[str]:
    """Keep only runner-executable DoH endpoints.

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


def _verdict(results: list[tuple[bool, str]]) -> tuple[str, str]:
    """Return (verdict, failure_category) for one hostname.

    verdict is ``resolved``, ``dns_unresolved``, or ``inconclusive``. A
    hostname is only DNS-confirmed unresolved when every endpoint that returned
    a DNS response agreed there is no public A/AAAA record and at least one
    endpoint answered at all.
    """

    if any(resolved for resolved, _ in results):
        return "resolved", "answered"
    responses = [category for _, category in results if category in ANSWER_CATEGORIES]
    if responses and all(category in _DNS_CONFIRMED_CATEGORIES for category in responses):
        return "dns_unresolved", ("nxdomain" if "nxdomain" in responses else "no_answer")
    failures = [category for _, category in results if category not in ANSWER_CATEGORIES]
    return "inconclusive", (failures[0] if failures else "transport_error")


def _source(name: object) -> str:
    match = _SOURCE_NAME.search(name) if isinstance(name, str) else None
    return match.group(1) if match else "other"


def _region(provider_name: str) -> str:
    suffix = provider_name.rsplit("_", 1)[-1].lower()
    return suffix if suffix in _REGIONS else "other"


def quarantine_unresolvable_proxy_hosts(config: dict[str, Any]) -> dict[str, Any]:
    """Remove only hostnames that configured DoH resolvers prove unresolvable."""

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
            "dns_inconclusive": 0,
            "resolver_disagreement": 0,
            "quarantined": 0,
            "by_source": {},
            "by_region": {},
            "by_protocol": {},
            "by_failure_category": {},
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
    counts: Counter[str] = Counter()
    dns_responses = 0
    by_source: Counter[str] = Counter()
    by_region: Counter[str] = Counter()
    by_protocol: Counter[str] = Counter()
    by_failure_category: Counter[str] = Counter()
    by_source_failure_category: dict[str, Counter[str]] = {}
    cache: dict[str, tuple[str, str]] = {}
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
                results = [probe_doh(endpoint, server) for endpoint in endpoints]
                cache[server] = _verdict(results)
            verdict, failure_category = cache[server]
            dns_responses += sum(1 for _, category in results if category in ANSWER_CATEGORIES)
            if verdict == "resolved":
                counts["resolved"] += 1
                kept.append(proxy)
                continue
            if verdict == "inconclusive":
                # Runner transport trouble is not node evidence: keep the node.
                counts["dns_inconclusive"] += 1
                counts["resolver_disagreement"] += 1
                kept.append(proxy)
                continue
            counts["unresolved"] += 1
            counts["quarantined"] += 1
            source = _source(proxy.get("name"))
            by_source[source] += 1
            by_region[_region(str(provider_name))] += 1
            by_protocol[protocol] += 1
            by_failure_category[failure_category] += 1
            by_source_failure_category.setdefault(source, Counter())[failure_category] += 1
        if not kept:
            raise ValidationError("proxy hostname qualification would empty a provider")
        provider["payload"] = kept
    if counts["hostname_nodes"] and dns_responses == 0:
        # Not one resolver returned any DNS response: the stage is inconclusive
        # (runner network trouble), not the inventory.
        raise ValidationError(
            "proxy hostname qualification is inconclusive: no resolver returned a DNS response"
        )
    return {
        "status": "passed",
        **{
            key: int(counts[key])
            for key in (
                "hostname_nodes",
                "ip_literal_nodes",
                "resolved",
                "unresolved",
                "dns_inconclusive",
                "resolver_disagreement",
                "quarantined",
            )
        },
        "by_source": dict(sorted(by_source.items())),
        "by_region": dict(sorted(by_region.items())),
        "by_protocol": dict(sorted(by_protocol.items())),
        "by_failure_category": dict(sorted(by_failure_category.items())),
        "by_source_failure_category": {
            source: dict(sorted(categories.items()))
            for source, categories in sorted(by_source_failure_category.items())
        },
    }
