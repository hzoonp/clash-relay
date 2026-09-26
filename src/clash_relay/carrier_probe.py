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

from .carrier_qualification import (
    MAX_FALLBACK_INVENTORY,
    MIN_SAMPLES_PER_CARRIER,
    OUTCOME_CATEGORIES,
    SAMPLER_VERSION,
    parse_carrier_aggregate_payload,
)
from .classify import proxy_fingerprint
from .errors import ValidationError
from .proxy_endpoint_qualification import _TCP_TYPES, _UDP_NATIVE_TYPES
from .runtime_names import parse_runtime_name_region, parse_runtime_source_name
from .util import stable_json

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
    eligible_tcp_endpoints: int
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

    def aggregate_counts(self) -> dict[str, int]:
        """Safe diversity counts shared by producer and candidate verifier."""
        geographic = {t.region for t in self.targets if t.region in GEOGRAPHIC_REGIONS}
        return {
            "sampled": self.sampled,
            "sampled_tcp_endpoints": self.sampled_tcp_endpoints,
            "skipped_unsupported": self.skipped_unsupported,
            "skipped_udp_native_endpoints": self.skipped_udp_native_endpoints,
            "geographic_regions_sampled": len(geographic),
            "protocols_sampled": len({t.protocol for t in self.targets}),
            "sources_sampled": len({t.source for t in self.targets}),
            "strata_sampled": len({(t.region, t.protocol, t.source) for t in self.targets}),
            "eligible_tcp_endpoints": self.eligible_tcp_endpoints,
        }


def _canonical_region(name: str) -> str:
    scope_region = parse_runtime_name_region(name)
    if scope_region is None:
        raise ValidationError("carrier probe candidate has invalid runtime identity")
    return scope_region if scope_region in GEOGRAPHIC_REGIONS else UNKNOWN_REGION


def _candidate_topology_digest(candidate: Mapping[str, Any]) -> str:
    """Hash the generated candidate with provider payload order normalized."""
    canonical = dict(candidate)
    providers = canonical.get("proxy-providers")
    if isinstance(providers, Mapping):
        canonical["proxy-providers"] = {
            name: {
                **dict(provider),
                "payload": sorted(provider["payload"], key=stable_json),
            }
            if isinstance(provider, Mapping) and isinstance(provider.get("payload"), list)
            else provider
            for name, provider in providers.items()
        }
    return hashlib.sha256(stable_json(canonical).encode()).hexdigest()


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
    # Bind endpoint evidence to the exact generated routing/topology candidate,
    # including policy-only changes that leave the endpoint inventory intact.
    plan_items.append("candidate:" + _candidate_topology_digest(candidate))
    probe_plan_id = _keyed_id(key, repository, "probe-plan", "\0".join(sorted(plan_items)))
    # A UDP occurrence on a probed TCP endpoint is one endpoint, not a skip.
    udp_only = udp_identities - set(by_identity)
    return CarrierProbeSample(
        tuple(selected), len(by_identity), len(udp_only), set_id, inventory_set_id, probe_plan_id
    )


def verify_carrier_candidate_binding(
    candidate: Mapping[str, Any],
    aggregate: Mapping[str, Any],
    *,
    key: bytes,
    repository: str,
) -> None:
    """Reject carrier evidence from any other generated candidate or HMAC key."""
    rows, _, sample_set_id = parse_carrier_aggregate_payload(aggregate)
    if (
        not isinstance(sample_set_id, str)
        or not isinstance(aggregate.get("inventory_set_id"), str)
        or not isinstance(aggregate.get("probe_plan_id"), str)
        or aggregate.get("sampler_version") != SAMPLER_VERSION
        or not rows
        or any(row.sampled is None for row in rows)
    ):
        raise ValidationError("carrier evidence lacks complete candidate binding")
    sampled_counts = {row.sampled for row in rows}
    if len(sampled_counts) != 1:
        raise ValidationError("carrier evidence sampled counts differ")
    sampled_count = next(iter(sampled_counts))
    plan = sample_probe_targets(
        candidate=candidate,
        key=key,
        repository=repository,
        max_targets=sampled_count if sampled_count else DEFAULT_MAX_TARGETS,
    )
    for name, expected in (
        ("sample_set_id", plan.sample_set_id),
        ("inventory_set_id", plan.inventory_set_id),
        ("probe_plan_id", plan.probe_plan_id),
    ):
        supplied = aggregate.get(name)
        if not isinstance(supplied, str) or not hmac.compare_digest(supplied, expected):
            raise ValidationError("carrier evidence does not match the current candidate")
    if plan.sampler_version != aggregate["sampler_version"]:
        raise ValidationError("carrier evidence sampler version does not match")
    expected_counts = plan.aggregate_counts()
    for row in rows:
        for name, expected_count in expected_counts.items():
            if getattr(row, name) != expected_count:
                raise ValidationError("carrier evidence plan counts do not match candidate")


def verify_carrier_candidate_binding_from_env(
    candidate: Mapping[str, Any],
    aggregate: Mapping[str, Any],
    *,
    env: Mapping[str, str],
) -> None:
    """Read the key only for an active evidence input; never serialize it."""
    key = env.get("CLASH_RELAY_CARRIER_HMAC_KEY", "").encode("utf-8")
    repository = env.get("GITHUB_REPOSITORY", "")
    if len(key) < 32 or not repository:
        raise ValidationError("carrier evidence requires a repository-bound HMAC key")
    verify_carrier_candidate_binding(candidate, aggregate, key=key, repository=repository)


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
    counts = sample.aggregate_counts()
    diverse = counts["geographic_regions_sampled"] >= 2 or counts["sources_sampled"] >= 2
    fallback = (
        sample.eligible_tcp_endpoints <= MAX_FALLBACK_INVENTORY
        and sample.sampled == sample.eligible_tcp_endpoints
    )
    sufficient = (
        sample.sampled >= MIN_SAMPLES_PER_CARRIER
        and len(outcomes) == sample.sampled
        and (diverse or fallback)
    )
    outcome_counts = dict.fromkeys(sorted(OUTCOME_CATEGORIES), 0)
    for target in sample.targets:
        result = outcomes[target.identity]
        outcome = result.get("outcome")
        if outcome not in OUTCOME_CATEGORIES or (outcome == "tcp_connected") != bool(
            result.get("reachable")
        ):
            raise ValidationError("carrier probe outcome is invalid")
        outcome_counts[outcome] += 1
    row = {
        **counts,
        "tested": len(outcomes),
        "reachable": reachable,
        "outcomes": outcome_counts,
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
