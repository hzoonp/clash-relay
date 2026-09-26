"""Private, bounded endpoint probes from explicitly configured carrier runners."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import math
import queue
import socket
import statistics
import threading
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .carrier_qualification import MIN_SAMPLES_PER_CARRIER, SAMPLER_VERSION
from .classify import proxy_fingerprint
from .errors import ValidationError
from .proxy_endpoint_qualification import _TCP_TYPES, _UDP_NATIVE_TYPES
from .runtime_names import parse_runtime_name_region, parse_runtime_source_name

CARRIERS = frozenset({"telecom", "unicom", "mobile"})
GEOGRAPHIC_REGIONS = frozenset({"HK", "TW", "SG", "JP", "US", "KR", "OTHER"})
UNKNOWN_REGION = "UNKNOWN"
DEFAULT_MAX_TARGETS = 12
MAX_TARGETS = 48
CONNECT_TIMEOUT_SECONDS = 2.0
DNS_TIMEOUT_SECONDS = 2.0
ATTEMPTS = 2


def assert_self_hosted_probe_environment(env: Mapping[str, str], carrier: str) -> None:
    """Defense-in-depth gate; workflow scheduling and environment provide trust."""
    if carrier not in CARRIERS:
        raise ValidationError("carrier probe requires a known carrier")
    if (
        env.get("GITHUB_ACTIONS") != "true"
        or env.get("RUNNER_ENVIRONMENT") != "self-hosted"
        or env.get("CLASH_RELAY_CARRIER_PROBE_ENABLED") != "true"
        or env.get("CLASH_RELAY_CARRIER_PROBE_NETWORK") != carrier
    ):
        raise ValidationError(
            "carrier probe requires an explicitly configured carrier self-hosted runner"
        )


def _keyed_id(key: bytes, repository: str, domain: str, value: str) -> str:
    if len(key) < 32 or not repository:
        raise ValidationError("carrier probe requires a repository-bound HMAC key")
    message = f"clash-relay/carrier/v1\0{repository}\0{domain}\0{value}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class ProbeTarget:
    region: str
    protocol: str
    source: str
    hostname: str
    port: int
    identity: str


@dataclass(frozen=True, slots=True)
class CarrierProbeSample:
    targets: tuple[ProbeTarget, ...]
    skipped_unsupported: int
    sample_set_id: str
    inventory_set_id: str
    probe_plan_id: str
    sampler_version: int = SAMPLER_VERSION

    @property
    def sampled(self) -> int:
        return len(self.targets)

    @property
    def sampled_tcp_endpoints(self) -> int:
        return self.sampled

    @property
    def skipped_udp_native_endpoints(self) -> int:
        return self.skipped_unsupported


def _canonical_region(name: str) -> str:
    scope_region = parse_runtime_name_region(name)
    if scope_region is None:
        raise ValidationError("carrier probe candidate has invalid runtime identity")
    return scope_region if scope_region in GEOGRAPHIC_REGIONS else UNKNOWN_REGION


def sample_probe_targets(
    *,
    candidate: Mapping[str, Any],
    key: bytes,
    repository: str,
    max_targets: int = DEFAULT_MAX_TARGETS,
) -> CarrierProbeSample:
    """Select the same HMAC-ranked region/protocol/source balanced target set."""
    if not 1 <= max_targets <= MAX_TARGETS:
        raise ValidationError("carrier probe max targets is outside the bounded range")
    providers = candidate.get("proxy-providers")
    if not isinstance(providers, Mapping):
        raise ValidationError("carrier probe candidate lacks proxy providers")
    occurrences: dict[str, list[ProbeTarget]] = {}
    udp_identities: set[str] = set()
    inventory_entries: set[str] = set()
    for provider in providers.values():
        payload = provider.get("payload") if isinstance(provider, Mapping) else None
        if not isinstance(payload, list):
            continue
        for proxy in payload:
            if not isinstance(proxy, Mapping):
                continue
            protocol, name = proxy.get("type"), proxy.get("name")
            server, port = proxy.get("server"), proxy.get("port")
            if (
                not isinstance(server, str)
                or not isinstance(port, int)
                or isinstance(port, bool)
                or not 1 <= port <= 65535
            ):
                continue
            identity = _keyed_id(key, repository, "endpoint", f"{server.lower()}\0{port}")
            if protocol not in _TCP_TYPES and protocol not in _UDP_NATIVE_TYPES:
                continue
            if not isinstance(name, str):
                raise ValidationError("carrier probe candidate has invalid runtime identity")
            region = _canonical_region(name)
            source = parse_runtime_source_name(name)
            if source is None:
                raise ValidationError("carrier probe candidate has invalid runtime identity")
            fingerprint = proxy_fingerprint(dict(proxy))
            inventory_entries.add(
                _keyed_id(
                    key,
                    repository,
                    "inventory-entry",
                    f"{identity}\0{protocol}\0{source}\0{region}\0{fingerprint}",
                )
            )
            if protocol in _UDP_NATIVE_TYPES:
                udp_identities.add(identity)
                continue
            target = ProbeTarget(region, str(protocol), source, server, port, identity)
            occurrences.setdefault(identity, []).append(target)
    by_identity: dict[str, ProbeTarget] = {}
    for identity, variants in occurrences.items():
        geographic = {variant.region for variant in variants if variant.region != UNKNOWN_REGION}
        if len(geographic) > 1:
            raise ValidationError("carrier probe endpoint has conflicting geographic regions")
        selected_region = next(iter(geographic), UNKNOWN_REGION)
        preferred = [variant for variant in variants if variant.region == selected_region]
        # Keyed tie-breaking avoids systematically favoring the first source ID.
        by_identity[identity] = min(
            preferred,
            key=lambda variant: _keyed_id(
                key,
                repository,
                "occurrence-rank",
                f"{identity}\0{variant.protocol}\0{variant.source}",
            ),
        )
    remaining = sorted(by_identity.values(), key=lambda t: t.identity)
    selected: list[ProbeTarget] = []
    regions: Counter[str] = Counter()
    protocols: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    strata: Counter[tuple[str, str, str]] = Counter()
    while remaining and len(selected) < max_targets:
        target = min(
            remaining,
            key=lambda t: (
                strata[t.region, t.protocol, t.source],
                regions[t.region],
                protocols[t.protocol],
                sources[t.source],
                t.identity,
            ),
        )
        remaining.remove(target)
        selected.append(target)
        regions[target.region] += 1
        protocols[target.protocol] += 1
        sources[target.source] += 1
        strata[target.region, target.protocol, target.source] += 1
    set_id = _keyed_id(
        key, repository, "sample-set", "\0".join(sorted(t.identity for t in selected))
    )
    inventory_set_id = _keyed_id(
        key, repository, "inventory-set", "\0".join(sorted(inventory_entries))
    )
    plan_items = [
        f"{target.identity}\0{target.region}\0{target.protocol}\0{target.source}"
        for target in selected
    ]
    probe_plan_id = _keyed_id(key, repository, "probe-plan", "\0".join(sorted(plan_items)))
    # A UDP occurrence on a probed TCP endpoint is one endpoint, not a skip.
    udp_only = udp_identities - set(by_identity)
    return CarrierProbeSample(
        tuple(selected), len(udp_only), set_id, inventory_set_id, probe_plan_id
    )


def _resolve_bounded(hostname: str, port: int, timeout: float) -> list[tuple[Any, ...]]:
    result: queue.Queue[object] = queue.Queue(maxsize=1)

    def resolve() -> None:
        try:
            result.put(socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM))
        except OSError as exc:
            result.put(exc)

    threading.Thread(target=resolve, daemon=True).start()
    try:
        answer = result.get(timeout=timeout)
    except queue.Empty:
        return []
    return answer if isinstance(answer, list) else []


def probe_tcp_endpoint(
    *, hostname: str, port: int, timeout: float = CONNECT_TIMEOUT_SECONDS, attempts: int = ATTEMPTS
) -> dict[str, Any]:
    """Resolve once, then attempt TCP with a per-attempt total deadline."""
    if not 0 < timeout <= CONNECT_TIMEOUT_SECONDS or not 1 <= attempts <= ATTEMPTS:
        raise ValidationError("carrier probe timeout/retry exceeds bound")
    addresses = _resolve_bounded(hostname, port, DNS_TIMEOUT_SECONDS)
    if not addresses:
        return {"reachable": False, "latency_ms": None, "outcome": "dns_failure"}
    public: list[tuple[Any, Any]] = []
    for family, _, _, _, sockaddr in addresses:
        try:
            if ipaddress.ip_address(sockaddr[0]).is_global:
                public.append((family, sockaddr))
        except ValueError:
            continue
    if not public:
        return {"reachable": False, "latency_ms": None, "outcome": "dns_failure"}
    best: float | None = None
    category = "connect_failure"
    for _ in range(attempts):
        deadline = time.monotonic() + timeout
        for family, sockaddr in public:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            started = time.perf_counter()
            try:
                with socket.socket(family, socket.SOCK_STREAM) as connection:
                    connection.settimeout(remaining)
                    connection.connect(sockaddr)
                measured = (time.perf_counter() - started) * 1000
                best = measured if best is None else min(best, measured)
                break
            except (OSError, TimeoutError) as exc:
                if isinstance(exc, (socket.timeout, TimeoutError)):
                    category = "connect_timeout"
                elif isinstance(exc, ConnectionRefusedError):
                    category = "connection_refused"
    return {
        "reachable": best is not None,
        "latency_ms": round(best, 3) if best is not None else None,
        "outcome": "tcp_connected" if best is not None else category,
    }


def build_carrier_probe_payload(
    *,
    carrier: str,
    sample: CarrierProbeSample,
    outcomes: Mapping[str, Mapping[str, Any]],
    collected_at_epoch: int,
) -> dict[str, Any]:
    """Project private per-target results into the strict aggregate schema."""
    if carrier not in CARRIERS or set(outcomes) != {t.identity for t in sample.targets}:
        raise ValidationError("carrier probe outcomes do not match the selected sample")
    reachable = 0
    latencies: list[float] = []
    for target in sample.targets:
        result = outcomes[target.identity]
        if result.get("reachable"):
            latency = result.get("latency_ms")
            if (
                not isinstance(latency, (float, int))
                or isinstance(latency, bool)
                or not math.isfinite(latency)
                or latency < 0
            ):
                raise ValidationError("carrier probe reachable result lacks valid latency")
            reachable += 1
            latencies.append(float(latency))
    latencies.sort()
    geographic_regions = {t.region for t in sample.targets if t.region in GEOGRAPHIC_REGIONS}
    protocols = {t.protocol for t in sample.targets}
    sources = {t.source for t in sample.targets}
    strata = {(t.region, t.protocol, t.source) for t in sample.targets}
    sufficient = (
        sample.sampled >= MIN_SAMPLES_PER_CARRIER
        and len(outcomes) == sample.sampled
        and len(strata) >= 2
    )
    row = {
        "sampled": sample.sampled,
        "sampled_tcp_endpoints": sample.sampled_tcp_endpoints,
        "tested": len(outcomes),
        "reachable": reachable,
        "skipped_unsupported": sample.skipped_unsupported,
        "skipped_udp_native_endpoints": sample.skipped_udp_native_endpoints,
        "geographic_regions_sampled": len(geographic_regions),
        "protocols_sampled": len(protocols),
        "sources_sampled": len(sources),
        "strata_sampled": len(strata),
        "sufficient_evidence": sufficient,
        "median_latency_ms": round(statistics.median(latencies), 3) if latencies else None,
        "p90_latency_ms": round(latencies[math.ceil(0.9 * len(latencies)) - 1], 3)
        if latencies
        else None,
    }
    return {
        "schema_version": 1,
        "collected_at_epoch": collected_at_epoch,
        "sample_set_id": sample.sample_set_id,
        "inventory_set_id": sample.inventory_set_id,
        "probe_plan_id": sample.probe_plan_id,
        "sampler_version": sample.sampler_version,
        "carriers": {carrier: row},
    }


def run_carrier_probe(
    *,
    carrier: str,
    candidate: Mapping[str, Any],
    key: bytes,
    repository: str,
    env: Mapping[str, str],
    max_targets: int = DEFAULT_MAX_TARGETS,
) -> dict[str, Any]:
    assert_self_hosted_probe_environment(env, carrier)
    sample = sample_probe_targets(
        candidate=candidate, key=key, repository=repository, max_targets=max_targets
    )
    outcomes = {
        target.identity: probe_tcp_endpoint(hostname=target.hostname, port=target.port)
        for target in sample.targets
    }
    return build_carrier_probe_payload(
        carrier=carrier, sample=sample, outcomes=outcomes, collected_at_epoch=int(time.time())
    )
