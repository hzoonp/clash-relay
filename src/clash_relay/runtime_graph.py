"""Canonical, side-effect-free view of a generated Mihomo runtime graph.

RuntimeGraph is the topology truth for generated candidates.  Audits and
post-generation transforms must consume this module instead of maintaining
private BFS/DFS implementations over proxy groups and providers.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .errors import ValidationError

BUILTIN_TARGETS = frozenset({"DIRECT", "REJECT", "PASS", "COMPATIBLE"})


@dataclass(frozen=True, slots=True)
class GraphReachability:
    groups: frozenset[str]
    providers: frozenset[str]
    proxies: frozenset[str]
    builtins: frozenset[str]
    unresolved: frozenset[str]


@dataclass(frozen=True, slots=True)
class CandidateArtifact:
    """One immutable logical stage of a production candidate.

    The nested mapping is deep-copied on construction/transition so callers do
    not accidentally mutate an earlier stage in place.  The fingerprint is a
    canonical JSON digest of the logical document, not a YAML byte digest.
    """

    stage: str
    document: dict[str, Any]

    @classmethod
    def from_document(cls, stage: str, document: Mapping[str, Any]) -> CandidateArtifact:
        if not stage:
            raise ValidationError("candidate artifact stage must not be empty")
        if not isinstance(document, Mapping):
            raise ValidationError("candidate artifact document must be a mapping")
        return cls(stage=stage, document=copy.deepcopy(dict(document)))

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            self.document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def transition(
        self,
        stage: str,
        transform: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> CandidateArtifact:
        next_document = copy.deepcopy(self.document)
        result = transform(next_document)
        if result is not None:
            if not isinstance(result, dict):
                raise ValidationError("candidate stage transform returned a non-mapping")
            next_document = result
        return CandidateArtifact.from_document(stage, next_document)


class RuntimeGraph:
    """Normalized, snapshot-based graph indexes for one Mihomo document.

    Construction detaches the graph from the mutable compiler/qualification
    document.  Public mapping properties also return detached snapshots, so
    topology can only change by constructing a new RuntimeGraph.
    """

    def __init__(
        self,
        *,
        candidate: Mapping[str, Any],
        groups: dict[str, dict[str, Any]],
        providers: dict[str, dict[str, Any]],
        proxies: dict[str, dict[str, Any]],
        provider_proxies: dict[str, frozenset[str]],
        provider_dialers: dict[str, frozenset[str]],
    ) -> None:
        self._candidate = copy.deepcopy(dict(candidate))
        self._groups = copy.deepcopy(groups)
        self._providers = copy.deepcopy(providers)
        self._proxies = copy.deepcopy(proxies)
        self._provider_proxies = dict(provider_proxies)
        self._provider_dialers = dict(provider_dialers)

    @property
    def candidate(self) -> dict[str, Any]:
        """Return a detached candidate snapshot."""

        return copy.deepcopy(self._candidate)

    @property
    def groups(self) -> dict[str, dict[str, Any]]:
        """Return detached group rows for read-only consumers."""

        return copy.deepcopy(self._groups)

    @property
    def providers(self) -> dict[str, dict[str, Any]]:
        return copy.deepcopy(self._providers)

    @property
    def proxies(self) -> dict[str, dict[str, Any]]:
        return copy.deepcopy(self._proxies)

    @property
    def provider_proxies(self) -> dict[str, frozenset[str]]:
        return dict(self._provider_proxies)

    @property
    def provider_dialers(self) -> dict[str, frozenset[str]]:
        return dict(self._provider_dialers)

    @classmethod
    def from_candidate(cls, candidate: Mapping[str, Any]) -> RuntimeGraph:
        if not isinstance(candidate, Mapping):
            raise ValidationError("runtime graph requires a candidate mapping")

        snapshot = copy.deepcopy(dict(candidate))
        raw_groups = snapshot.get("proxy-groups", [])
        if not isinstance(raw_groups, list):
            raise ValidationError("runtime graph requires proxy-groups to be a list")
        groups: dict[str, dict[str, Any]] = {}
        for row in raw_groups:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                raise ValidationError("runtime graph found a malformed proxy group")
            name = str(row["name"])
            if name in groups:
                raise ValidationError(f"runtime graph found duplicate group {name!r}")
            groups[name] = row

        raw_proxies = snapshot.get("proxies", [])
        if not isinstance(raw_proxies, list):
            raise ValidationError("runtime graph requires proxies to be a list")
        proxies: dict[str, dict[str, Any]] = {}
        for proxy in raw_proxies:
            if not isinstance(proxy, dict) or not isinstance(proxy.get("name"), str):
                raise ValidationError("runtime graph found a malformed top-level proxy")
            proxies[str(proxy["name"])] = proxy

        raw_providers = snapshot.get("proxy-providers", {})
        if not isinstance(raw_providers, dict):
            raise ValidationError("runtime graph requires proxy-providers to be a mapping")
        providers: dict[str, dict[str, Any]] = {}
        provider_proxies: dict[str, frozenset[str]] = {}
        provider_dialers: dict[str, frozenset[str]] = {}
        for provider_name, provider in raw_providers.items():
            if not isinstance(provider_name, str) or not isinstance(provider, dict):
                raise ValidationError("runtime graph found a malformed proxy provider")
            payload = provider.get("payload", [])
            if not isinstance(payload, list):
                raise ValidationError(
                    f"runtime graph provider {provider_name!r} has a non-list payload"
                )
            names: set[str] = set()
            dialers: set[str] = set()
            for proxy in payload:
                if not isinstance(proxy, dict) or not isinstance(proxy.get("name"), str):
                    raise ValidationError(
                        f"runtime graph provider {provider_name!r} has a malformed proxy"
                    )
                name = str(proxy["name"])
                names.add(name)
                proxies.setdefault(name, proxy)
                dialer = proxy.get("dialer-proxy")
                if isinstance(dialer, str) and dialer:
                    dialers.add(dialer)
            providers[provider_name] = provider
            provider_proxies[provider_name] = frozenset(names)
            provider_dialers[provider_name] = frozenset(dialers)

        return cls(
            candidate=snapshot,
            groups=groups,
            providers=providers,
            proxies=proxies,
            provider_proxies=provider_proxies,
            provider_dialers=provider_dialers,
        )

    def group_members(self, name: str) -> tuple[str, ...]:
        group = self._groups.get(name)
        if group is None:
            raise ValidationError(f"runtime graph group {name!r} is missing")
        members = group.get("proxies", [])
        if not isinstance(members, list) or not all(isinstance(item, str) for item in members):
            raise ValidationError(f"runtime graph group {name!r} has invalid proxy references")
        return tuple(str(item) for item in members)

    def group_uses(self, name: str) -> tuple[str, ...]:
        group = self._groups.get(name)
        if group is None:
            raise ValidationError(f"runtime graph group {name!r} is missing")
        uses = group.get("use", [])
        if uses is None:
            return ()
        if not isinstance(uses, list) or not all(isinstance(item, str) for item in uses):
            raise ValidationError(f"runtime graph group {name!r} has invalid provider references")
        return tuple(str(item) for item in uses)

    def reachable_groups(self, starts: Iterable[str]) -> frozenset[str]:
        """Return group-only reachability for UI/rule presentation audits."""

        found: set[str] = set()
        pending = [str(item) for item in starts if str(item) in self._groups]
        while pending:
            name = pending.pop()
            if name in found:
                continue
            found.add(name)
            pending.extend(
                member
                for member in self.group_members(name)
                if member in self._groups and member not in found
            )
        return frozenset(found)

    def group_cycles(self) -> tuple[tuple[str, ...], ...]:
        """Return deterministic proxy-group reference cycles."""

        visiting: set[str] = set()
        visited: set[str] = set()
        stack: list[str] = []
        found: list[tuple[str, ...]] = []

        def visit(name: str) -> None:
            if name in visiting:
                try:
                    start = stack.index(name)
                except ValueError:  # pragma: no cover - defensive invariant
                    start = 0
                found.append((*stack[start:], name))
                return
            if name in visited:
                return
            visiting.add(name)
            stack.append(name)
            children = sorted(
                member for member in self.group_members(name) if member in self._groups
            )
            for child in children:
                visit(child)
            stack.pop()
            visiting.remove(name)
            visited.add(name)

        for name in sorted(self._groups):
            visit(name)
        return tuple(found)

    def walk(self, target: str) -> GraphReachability:
        groups: set[str] = set()
        providers: set[str] = set()
        proxies: set[str] = set()
        builtins: set[str] = set()
        unresolved: set[str] = set()
        visiting: set[str] = set()

        def visit(reference: str) -> None:
            if reference in BUILTIN_TARGETS:
                builtins.add(reference)
                return
            if reference in self._proxies:
                proxies.add(reference)
                dialer = self._proxies[reference].get("dialer-proxy")
                if isinstance(dialer, str) and dialer:
                    visit(dialer)
                return
            group = self._groups.get(reference)
            if group is None:
                unresolved.add(reference)
                return
            if reference in visiting:
                raise ValidationError(f"runtime graph found a cycle at {reference!r}")
            if reference in groups:
                return
            visiting.add(reference)
            groups.add(reference)
            for provider_name in self.group_uses(reference):
                providers.add(provider_name)
                if provider_name not in self._providers:
                    unresolved.add(provider_name)
                    continue
                for proxy_name in self._provider_proxies.get(provider_name, frozenset()):
                    visit(proxy_name)
                for dialer in self._provider_dialers.get(provider_name, frozenset()):
                    visit(dialer)
            for member in self.group_members(reference):
                visit(member)
            visiting.remove(reference)

        visit(target)
        return GraphReachability(
            groups=frozenset(groups),
            providers=frozenset(providers),
            proxies=frozenset(proxies),
            builtins=frozenset(builtins),
            unresolved=frozenset(unresolved),
        )

    def walk_resolved(self, target: str) -> GraphReachability:
        """Walk a target and fail closed if any runtime reference is unresolved."""

        result = self.walk(target)
        if result.unresolved:
            unresolved = ", ".join(sorted(result.unresolved))
            raise ValidationError(
                f"runtime graph target {target!r} has unresolved references: {unresolved}"
            )
        return result

    def reachable_providers(self, target: str, *, require_resolved: bool = False) -> frozenset[str]:
        reachability = self.walk_resolved(target) if require_resolved else self.walk(target)
        return reachability.providers

    def provider_order(self, target: str) -> tuple[str, ...]:
        """Return providers in deterministic group traversal order.

        This query intentionally mirrors the manual-selection surface: it walks
        group references breadth-first and ignores proxy dialers, while all
        topology validation remains owned by ``walk``/``walk_resolved``.
        """

        pending = [target]
        visited: set[str] = set()
        providers: list[str] = []
        while pending:
            name = pending.pop(0)
            if name in visited:
                continue
            visited.add(name)
            if name not in self._groups:
                continue
            for provider_name in self.group_uses(name):
                if provider_name in self._providers and provider_name not in providers:
                    providers.append(provider_name)
            pending.extend(member for member in self.group_members(name) if member in self._groups)
        return tuple(providers)

    def reachable_sources(
        self,
        target: str,
        *,
        proxy_sources: Mapping[str, str],
        provider_sources: Mapping[str, set[str] | frozenset[str]] | None = None,
        require_resolved: bool = False,
    ) -> frozenset[str]:
        reachability = self.walk_resolved(target) if require_resolved else self.walk(target)
        found = {str(proxy_sources[name]) for name in reachability.proxies if name in proxy_sources}
        if provider_sources is not None:
            for provider in reachability.providers:
                found.update(str(item) for item in provider_sources.get(provider, ()))
        return frozenset(found)
