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
quarantine accounting deduplicates logical nodes by the full proxy fingerprint.
Hostname probes are cached per hostname, while provider copies remain separate
runtime entries.
"""

from __future__ import annotations

import ipaddress
import urllib.parse
from collections import Counter
from typing import Any

from .classify import proxy_fingerprint
from .dns_wire import ANSWER_CATEGORIES, probe_doh
from .errors import ValidationError
from .runtime_names import parse_runtime_source_name

_REGIONS = frozenset({"hk", "tw", "sg", "jp", "us", "kr", "other"})
_DNS_CONFIRMED_CATEGORIES = frozenset({"nxdomain", "no_answer"})
_MIN_AGREEING_NEGATIVES = 2
_UniqueKey = tuple[str, str]


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


def _resolver_authority(endpoint: str) -> tuple[str, str, int, str] | None:
    """Return the normalized resolver authority, or None when unusable."""

    try:
        parsed = urllib.parse.urlsplit(str(endpoint))
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    return (
        parsed.scheme.lower(),
        parsed.hostname.lower().rstrip("."),
        port or 443,
        parsed.path or "/",
    )


def _doh_endpoints(resolvers: list[Any]) -> list[str]:
    """Keep one endpoint per distinct resolver authority.

    The runner can probe HTTPS DoH endpoints only; a ``system`` entry names the
    OS resolver and other non-HTTPS entries are not DoH, so they cannot take
    part in this global preflight and are excluded instead of crashing the
    stage. The same resolver declared twice (same host, port, and path) is one
    resolver with one vote — negative quorum requires distinct authorities.
    """

    endpoints: list[str] = []
    seen: set[tuple[str, str, int, str]] = set()
    for resolver in resolvers:
        authority = _resolver_authority(str(resolver))
        if authority is None:
            continue
        if authority in seen:
            continue
        seen.add(authority)
        endpoints.append(str(resolver))
    return endpoints


def _merge_dual_records(records: list[tuple[bool, str]]) -> tuple[bool, str]:
    """Merge the A and AAAA probe results of one endpoint.

    A public answer wins. Otherwise the merged record is a definitive negative
    only when every response was a definitive negative; any server failure,
    malformed response, or transport failure stays inconclusive and must never
    be downgraded into negative evidence.
    """

    if any(answered for answered, _ in records):
        return True, "answered"
    categories = [category for _, category in records]
    if "nxdomain" in categories and all(
        category in _DNS_CONFIRMED_CATEGORIES for category in categories
    ):
        return False, "nxdomain"
    if categories and all(category in _DNS_CONFIRMED_CATEGORIES for category in categories):
        return False, "no_answer"
    non_definitive = [
        category for category in categories if category not in _DNS_CONFIRMED_CATEGORIES
    ]
    return False, (non_definitive[0] if non_definitive else "no_answer")


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
    non_definitive = [
        category for _, category in results if category not in _DNS_CONFIRMED_CATEGORIES
    ]
    first_failure = (
        negatives[0] if negatives else (non_definitive[0] if non_definitive else "transport_error")
    )
    return "inconclusive", first_failure


def _source(name: object) -> str:
    source = parse_runtime_source_name(name)
    if source is None:
        raise ValidationError("proxy hostname qualification found an invalid runtime source name")
    return source


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
        "unique_quarantined_hostnames": 0,
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
    by_source: Counter[str] = Counter()
    by_region: Counter[str] = Counter()
    by_protocol: Counter[str] = Counter()
    by_failure_category: Counter[str] = Counter()
    by_source_failure_category: dict[str, Counter[str]] = {}
    unique_quarantined: dict[_UniqueKey, tuple[str, str]] = {}
    quarantined_hostnames: set[str] = set()
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
            by_source[source] += 1
            by_region[_region(str(provider_name))] += 1
            by_protocol[protocol] += 1
            by_failure_category[failure_category] += 1
            by_source_failure_category.setdefault(source, Counter())[failure_category] += 1
            key: _UniqueKey = (source, proxy_fingerprint(proxy))
            unique_quarantined.setdefault(key, (_region(str(provider_name)), failure_category))
            quarantined_hostnames.add(server)
        if not kept:
            raise ValidationError("proxy hostname qualification would empty a provider")
        provider["payload"] = kept
    if counts["hostname_nodes"] and dns_responses == 0:
        # Not one resolver returned any DNS response: the stage is inconclusive
        # (runner network trouble), not the inventory.
        raise ValidationError(
            "proxy hostname qualification is inconclusive: no resolver returned a DNS response"
        )
    # Aggregate dimensions are runtime-entry counts; unique_quarantined_nodes
    # carries the full-fingerprint logical-node view for the same removals.

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
        "unique_quarantined_hostnames": len(quarantined_hostnames),
        "by_source": dict(sorted(by_source.items())),
        "by_region": dict(sorted(by_region.items())),
        "by_protocol": dict(sorted(by_protocol.items())),
        "by_failure_category": dict(sorted(by_failure_category.items())),
        "by_source_failure_category": {
            source: dict(sorted(categories.items()))
            for source, categories in sorted(by_source_failure_category.items())
        },
    }
