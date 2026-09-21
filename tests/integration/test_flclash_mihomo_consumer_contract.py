from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
import yaml

from clash_relay.builder import build_candidate
from clash_relay.mihomo import validate_with_mihomo
from clash_relay.runtime_graph import RuntimeGraph

pytestmark = pytest.mark.integration


def _binary() -> Path:
    value = os.environ.get("MIHOMO_BIN")
    if not value:
        pytest.skip("MIHOMO_BIN is not set")
    return Path(value)


def _subscription(path: Path, rows: list[dict]) -> str:
    path.write_text(yaml.safe_dump({"proxies": rows}, sort_keys=False), encoding="utf-8")
    return path.resolve().as_uri()


def _http(name: str, server: str, port: int) -> dict:
    return {"name": name, "type": "http", "server": server, "port": port}


def _acl_fixture_fetcher(url: str, **kwargs) -> str:
    # The consumer contract is about generated Mihomo/FlClash-facing structure,
    # not upstream ACL4SSR availability. Keep the rule input deterministic and
    # offline while still exercising the canonical ACL compilation path.
    return "DOMAIN-SUFFIX,fictional-consumer.example\n"


def _reachable_servers(graph: RuntimeGraph, proxy_names: frozenset[str]) -> set[str]:
    runtime_proxies = graph.proxies
    return {str(runtime_proxies[name]["server"]) for name in proxy_names}


def _canonical_project(repo_root: Path, tmp_path: Path) -> tuple[dict[str, Path], dict[str, str]]:
    root = tmp_path / "canonical-consumer"
    root.mkdir()
    for name in ("config.yaml", "subscriptions.yaml", "policies.yaml"):
        shutil.copy2(repo_root / name, root / name)
    shutil.copytree(repo_root / "policies", root / "policies")
    shutil.copytree(repo_root / "rules", root / "rules")

    config_path = root / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["generation"]["allow_file_subscription_urls"] = True
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    subscription_1 = _subscription(
        root / "subscription-1.yaml",
        [
            _http("US Standard", "sub1-standard.invalid.example", 21001),
            _http("US Exactly 2x", "sub1-two.invalid.example", 21002),
            _http("US 2.01x", "sub1-over.invalid.example", 21003),
            _http("US EMBY 1x", "sub1-emby.invalid.example", 21004),
        ],
    )
    subscription_2 = _subscription(
        root / "subscription-2.yaml",
        [_http("US General 02", "sub2-general.invalid.example", 22001)],
    )
    subscription_3 = _subscription(
        root / "subscription-3.yaml",
        [_http("JP General 03", "sub3-general.invalid.example", 23001)],
    )
    subscription_4 = _subscription(
        root / "subscription-4.yaml",
        [_http("SG General 04", "sub4-general.invalid.example", 24001)],
    )
    subscription_5 = _subscription(
        root / "subscription-5.yaml",
        [_http("HK General 05", "sub5-general.invalid.example", 25001)],
    )
    return (
        {
            "config_path": config_path,
            "subscriptions_path": root / "subscriptions.yaml",
            "policies_path": root / "policies.yaml",
        },
        {
            "SUBSCRIPTION_1_URL": subscription_1,
            "SUBSCRIPTION_2_URL": subscription_2,
            "SUBSCRIPTION_3_URL": subscription_3,
            "SUBSCRIPTION_4_URL": subscription_4,
            "SUBSCRIPTION_5_URL": subscription_5,
        },
    )


def _assert_ai_is_fail_closed(document: dict) -> None:
    graph = RuntimeGraph.from_candidate(document)
    ai = graph.walk_resolved("人工智能")
    assert not ai.proxies
    assert ai.builtins == frozenset({"REJECT"})
    assert "DIRECT" not in ai.builtins


def test_flclash_facing_candidate_preserves_source_isolation_and_loads_in_real_mihomo(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    paths, env = _canonical_project(repo_root, tmp_path)
    result = build_candidate(**paths, env=env, rule_fetcher=_acl_fixture_fetcher)

    reports = {row["id"]: row for row in result.report["subscriptions"]}
    restricted = reports["subscription_1"]
    assert restricted["status"] == "ok"
    assert restricted["nodes"] == 2
    assert restricted["filtered_by_name"] == 1
    assert restricted["filtered_over_multiplier"] == 1

    assert "US Standard" in result.yaml_text
    assert "US Exactly 2x" in result.yaml_text
    assert "US 2.01x" not in result.yaml_text
    assert "US EMBY 1x" not in result.yaml_text

    document = yaml.safe_load(result.yaml_text)
    graph = RuntimeGraph.from_candidate(document)

    dns = document["dns"]
    assert dns["enable"] is True
    assert dns["enhanced-mode"] == "fake-ip"
    assert dns["respect-rules"] is True
    assert dns["direct-nameserver-follow-policy"] is True
    assert dns["fallback"] == []
    assert dns["nameserver-policy"]
    assert all(str(key).startswith("rule-set:") for key in dns["nameserver-policy"])
    assert all(
        str(key).split(":", 1)[1] in document["rule-providers"]
        for key in dns["nameserver-policy"]
    )

    tun = document["tun"]
    assert tun["enable"] is True
    assert tun["stack"] == "mixed"
    assert tun["dns-hijack"] == ["any:53", "tcp://any:53"]
    assert tun["auto-route"] is True
    assert tun["auto-detect-interface"] is True
    assert tun["strict-route"] is True
    assert result.report["dns_leak_audit"]["status"] == "passed"
    assert result.report["dns_routing_policy"]["status"] == "compiled"

    general = graph.walk_resolved("代理选择")
    assert general.providers
    general_servers = _reachable_servers(graph, general.proxies)
    assert not any(server.startswith("sub1-") for server in general_servers)
    assert "sub2-general.invalid.example" in general_servers

    ai = graph.walk_resolved("人工智能")
    assert ai.providers
    ai_proxy_names = ai.proxies
    ai_servers = _reachable_servers(graph, ai_proxy_names)
    assert ai_servers == {
        "sub1-standard.invalid.example",
        "sub1-two.invalid.example",
    }
    assert "DIRECT" not in ai.builtins

    # "AI direct via subscription_1" means one proxy hop, not Mihomo DIRECT and
    # not a relay/dialer chain. The canonical topology has no chains, and every
    # AI-reachable runtime proxy must therefore have no dialer-proxy field.
    runtime_proxies = graph.proxies
    assert all("dialer-proxy" not in runtime_proxies[name] for name in ai_proxy_names)

    # FlClash consumes Mihomo configuration. CI proves that the exact
    # consumer-facing YAML is accepted and starts on the pinned real Mihomo
    # cores; it intentionally does not claim GUI/device-specific FlClash tests.
    candidate = tmp_path / "flclash-facing.yaml"
    candidate.write_text(result.yaml_text, encoding="utf-8")
    report = validate_with_mihomo(_binary(), candidate, startup_seconds=1.0)
    assert report["config_test"] == "passed"
    assert report["startup_smoke"] == "passed"


def test_ai_fails_closed_when_subscription_1_fetch_fails_even_if_general_sources_work(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    paths, env = _canonical_project(repo_root, tmp_path)
    missing = paths["config_path"].parent / "missing-subscription-1.yaml"
    env["SUBSCRIPTION_1_URL"] = missing.resolve().as_uri()

    result = build_candidate(**paths, env=env, rule_fetcher=_acl_fixture_fetcher)
    reports = {row["id"]: row for row in result.report["subscriptions"]}
    assert reports["subscription_1"]["status"] == "failed"
    assert all(
        reports[source_id]["status"] == "ok"
        for source_id in ("subscription_2", "subscription_3", "subscription_4", "subscription_5")
    )

    _assert_ai_is_fail_closed(result.config)
    graph = RuntimeGraph.from_candidate(result.config)
    general = graph.walk_resolved("代理选择")
    assert _reachable_servers(graph, general.proxies) == {
        "sub2-general.invalid.example",
        "sub3-general.invalid.example",
        "sub4-general.invalid.example",
        "sub5-general.invalid.example",
    }


def test_ai_fails_closed_when_subscription_1_nodes_are_all_rejected_by_admission(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    paths, env = _canonical_project(repo_root, tmp_path)
    root = paths["config_path"].parent
    env["SUBSCRIPTION_1_URL"] = _subscription(
        root / "subscription-1-rejected.yaml",
        [
            _http("US 2.01x", "sub1-over-only.invalid.example", 21101),
            _http("US EMBY 1x", "sub1-emby-only.invalid.example", 21102),
        ],
    )

    result = build_candidate(**paths, env=env, rule_fetcher=_acl_fixture_fetcher)
    restricted = next(
        row for row in result.report["subscriptions"] if row["id"] == "subscription_1"
    )
    assert restricted["status"] == "ok"
    assert restricted["nodes"] == 0
    assert restricted["filtered_by_name"] == 1
    assert restricted["filtered_over_multiplier"] == 1

    _assert_ai_is_fail_closed(result.config)
    graph = RuntimeGraph.from_candidate(result.config)
    general = graph.walk_resolved("代理选择")
    general_servers = _reachable_servers(graph, general.proxies)
    assert general_servers
    assert not any(server.startswith("sub1-") for server in general_servers)
