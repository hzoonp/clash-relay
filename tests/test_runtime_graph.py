from __future__ import annotations

import pytest

from clash_relay.errors import ValidationError
from clash_relay.runtime_graph import CandidateArtifact, RuntimeGraph


def _candidate() -> dict:
    return {
        "proxies": [{"name": "direct-proxy", "type": "direct"}],
        "proxy-providers": {
            "provider-a": {
                "payload": [
                    {"name": "node-a", "type": "direct", "dialer-proxy": "dialer"},
                ]
            },
            "provider-b": {
                "payload": [{"name": "node-b", "type": "direct"}],
            },
        },
        "proxy-groups": [
            {"name": "dialer", "type": "select", "proxies": ["DIRECT"]},
            {"name": "nested", "type": "select", "use": ["provider-a"], "proxies": []},
            {"name": "reserve", "type": "select", "use": ["provider-b"], "proxies": []},
            {
                "name": "public",
                "type": "select",
                "proxies": ["nested", "reserve", "direct-proxy"],
            },
        ],
    }


def test_runtime_graph_walks_groups_providers_proxies_and_dialers() -> None:
    graph = RuntimeGraph.from_candidate(_candidate())
    result = graph.walk("public")
    assert result.groups == frozenset({"public", "nested", "reserve", "dialer"})
    assert result.providers == frozenset({"provider-a", "provider-b"})
    assert result.proxies == frozenset({"node-a", "node-b", "direct-proxy"})
    assert result.builtins == frozenset({"DIRECT"})
    assert result.unresolved == frozenset()


def test_runtime_graph_owns_provider_order_and_source_reachability() -> None:
    graph = RuntimeGraph.from_candidate(_candidate())
    assert graph.provider_order("public") == ("provider-a", "provider-b")
    assert graph.reachable_providers("public") == frozenset({"provider-a", "provider-b"})
    assert graph.reachable_groups({"public"}) == frozenset({"public", "nested", "reserve"})
    assert graph.group_cycles() == ()
    assert graph.reachable_sources(
        "public",
        proxy_sources={"node-a": "primary", "node-b": "secondary"},
        provider_sources={"provider-a": {"primary"}, "provider-b": {"secondary"}},
        require_resolved=True,
    ) == frozenset({"primary", "secondary"})


def test_runtime_graph_reports_unresolved_references_and_cycles() -> None:
    candidate = _candidate()
    candidate["proxy-groups"].append(
        {"name": "missing-ref", "type": "select", "proxies": ["does-not-exist"]}
    )
    graph = RuntimeGraph.from_candidate(candidate)
    assert graph.walk("missing-ref").unresolved == frozenset({"does-not-exist"})
    with pytest.raises(ValidationError, match="unresolved references"):
        graph.walk_resolved("missing-ref")

    cyclic = {
        "proxy-providers": {},
        "proxy-groups": [
            {"name": "a", "type": "select", "proxies": ["b"]},
            {"name": "b", "type": "select", "proxies": ["a"]},
        ],
    }
    cyclic_graph = RuntimeGraph.from_candidate(cyclic)
    assert cyclic_graph.group_cycles() == (("a", "b", "a"),)
    with pytest.raises(ValidationError, match="cycle"):
        cyclic_graph.walk("a")


def test_runtime_graph_is_detached_from_source_and_returned_snapshots() -> None:
    candidate = _candidate()
    graph = RuntimeGraph.from_candidate(candidate)

    candidate["proxy-groups"][3]["proxies"].append("REJECT")
    candidate["proxy-providers"]["provider-a"]["payload"][0]["name"] = "mutated-source"

    detached_candidate = graph.candidate
    detached_groups = graph.groups
    detached_candidate["proxy-groups"][3]["proxies"].append("PASS")
    detached_groups["public"]["proxies"].append("COMPATIBLE")

    assert graph.group_members("public") == ("nested", "reserve", "direct-proxy")
    assert graph.provider_proxies["provider-a"] == frozenset({"node-a"})
    assert graph.walk("public").proxies == frozenset({"node-a", "node-b", "direct-proxy"})


def test_candidate_artifact_transitions_do_not_mutate_previous_stage() -> None:
    first = CandidateArtifact.from_document("generated", {"value": {"count": 1}})

    def increment(document: dict) -> None:
        document["value"]["count"] += 1

    second = first.transition("qualified", increment)
    assert first.document["value"]["count"] == 1
    assert second.document["value"]["count"] == 2
    assert first.fingerprint != second.fingerprint
