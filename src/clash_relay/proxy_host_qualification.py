"""Aggregate-only pre-publish admission for proxy server hostnames.

The runner probes each hostname through the candidate's DoH
``proxy-server-nameserver`` endpoints using RFC 8484 wireformat. Authority
boundary: this stage only filters hostnames that DNS proves unresolvable from
a data-center vantage point. It is never evidence of China Telecom / Unicom /
Mobile quality — a hostname the runner cannot resolve may still resolve fine
on a carrier access network.

Failure semantics are deliberately anti-false-kill:

- any resolver returning a public A (or AAAA when the candidate enables DNS
  IPv6) record resolves the hostname;
- quarantining requires at least two independent DoH endpoints to agree on a
  negative answer (NXDOMAIN or NOERROR with no usable address) — a single
  misbehaving resolver can never kill a node;
- one negative verdict combined with transport failures elsewhere is
  inconclusive and keeps the node;
- runner transport failures never quarantine a node, and a stage in which no
  resolver returned any DNS response fails closed instead of mass-quarantining
  the inventory.

Probe evidence is cached per hostname (never reused across hostnames), and
quarantine accounting is deduplicated to unique physical endpoints
(source, server, port, protocol); the same node replicated across providers is
one unique node with multiple runtime entries.
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
_MIN_AGREEING_NEGATIVES = 2
_UniqueKey = tuple[str, str, str, str]


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


def _merge_dual_records(records: list[tuple[bool, str]]) -> tuple[bool, str]:
    """Merge the A and AAAA probe results of one endpoint."""

    if any(answered for answered, _ in records):
        return True, "answered"
    categories = [category for _, category in records]
    if "nxdomain" in categories:
        return False, "nxdomain"
    responses = [category for category in categories if category in ANSWER_CATEGORIES]
    if responses and all(category in _DNS_CONFIRMED_CATEGORIES for category in responses):
        return False, "no_answer"
    transports = [category for category in categories if category not in ANSWER_CATEGORIES]
    return False, (transports[0] if transports else "no_answer")


def _probe_hostname(endpoint: str, hostname: str, *, allow_aaaa: bool) -> tuple[bool, str]:
    answered, category = probe_doh(endpoint, hostname)
    if answered or not allow_aaaa:
        return answered, category
    # DNS IPv6 is enabled: a hostname with only an AAAA record must qualify.
    aaaa_answered, aaaa_category = probe_doh(endpoint, hostname, qtype=28)
    return _merge_dual_records([(answered, category), (aaaa_answered, aaaa_category)])


def _verdict(results: list[tuple[bool, str]]) -> tuple[str, str]:
    """Return (verdict, failure_category) for one hostname.

    verdict is ``resolved``, ``dns_unresolved``, or ``inconclusive``. A
    hostname is only DNS-confirmed unresolved when at least
    ``_MIN_AGREEING_NEGATIVES`` independent endpoints returned a negative
    answer (NXDOMAIN or a NOERROR response with no public address).
    """

    if any(resolved for resolved, _ in results):
        return "resolved", "answered"
    negatives = [category for _, category in results if category in _DNS_CONFIRMED_CATEGORIES]
    if len(negatives) >= _MIN_AGREEING_NEGATIVES:
        return "dns_unresolved", ("nxdomain" if "nxdomain" in negatives else "no_answer")
    failures = [category for _, category in results if category not in ANSWER_CATEGORIES]
    first_failure = negatives[0] if negatives else (failures[0] if failures else "transport_error")
    return "inconclusive", first_failure


def _source(name: object) -> str:
    match = _SOURCE_NAME.search(name) if isinstance(name, str) else None
    return match.group(1) if match else "other"


def _region(provider_name: str) -> str:
    suffix = provider_name.rsplit("_", 1)[-1].lower()
    return suffix if suffix in _REGIONS else "other"


def _empty_report() -> dict[str, Any]:
    return {
        "status": "skipped",
        "hostname_nodes": 0,
        "ip_literal_nodes": 0,
        "resolved": 0,
        "unresolved": 0,
        "dns_inconclusive": 0,
        "resolver_disagreement": 0,
        "quarantined": 0,
        "unique_quarantined_nodes": 0,
        "by_source": {},
        "by_region": {},
        "by_protocol": {},
        "by_failure_category": {},
    }


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
        return _empty_report()
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
    allow_aaaa = bool(dns.get("ipv6", False)) if isinstance(dns, dict) else False
    counts: Counter[str] = Counter()
    dns_responses = 0
    unique_quarantined: dict[_UniqueKey, tuple[str, str]] = {}
    cache: dict[str, tuple[list[tuple[bool, str]], str, str]] = {}
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
                records = [
                    _probe_hostname(endpoint, server, allow_aaaa=allow_aaaa)
                    for endpoint in endpoints
                ]
                verdict, failure_category = _verdict(records)
                # Cache the full per-endpoint evidence together with the
                # verdict; evidence is never taken from another hostname.
                cache[server] = (records, verdict, failure_category)
                dns_responses += sum(1 for _, category in records if category in ANSWER_CATEGORIES)
            _, verdict, failure_category = cache[server]
            if verdict == "resolved":
                counts["resolved"] += 1
                kept.append(proxy)
                continue
            if verdict == "inconclusive":
                # Runner transport trouble or an uncorroborated negative is
                # not node evidence: keep the node.
                counts["dns_inconclusive"] += 1
                counts["resolver_disagreement"] += 1
                kept.append(proxy)
                continue
            counts["unresolved"] += 1
            counts["quarantined"] += 1
            source = _source(proxy.get("name"))
            key: _UniqueKey = (
                source,
                server,
                str(proxy.get("port", "")),
                protocol,
            )
            unique_quarantined.setdefault(key, (_region(str(provider_name)), failure_category))
        if not kept:
            raise ValidationError("proxy hostname qualification would empty a provider")
        provider["payload"] = kept
    if counts["hostname_nodes"] and dns_responses == 0:
        # Not one resolver returned any DNS response: the stage is inconclusive
        # (runner network trouble), not the inventory.
        raise ValidationError(
            "proxy hostname qualification is inconclusive: no resolver returned a DNS response"
        )
    by_source: Counter[str] = Counter(key[0] for key in unique_quarantined)
    by_region: Counter[str] = Counter(region for region, _ in unique_quarantined.values())
    by_protocol: Counter[str] = Counter(key[3] for key in unique_quarantined)
    by_failure_category: Counter[str] = Counter(
        category for _, category in unique_quarantined.values()
    )
    by_source_failure_category: dict[str, Counter[str]] = {}
    for key, (_region_name, category) in unique_quarantined.items():
        by_source_failure_category.setdefault(key[0], Counter())[category] += 1
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
