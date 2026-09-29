from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from clash_relay.builder import build_candidate
from clash_relay.config_loader import load_project
from clash_relay.download_isolation import audit_download_declarations, audit_download_rule_order
from clash_relay.errors import ConfigurationError, ValidationError
from clash_relay.production_audit import _runtime_source_maps, audit_production_candidate
from clash_relay.runtime_graph import RuntimeGraph


def _candidate(repo_root: Path):
    paths = {
        "config_path": repo_root / "config.yaml",
        "subscriptions_path": repo_root / "subscriptions.yaml",
        "policies_path": repo_root / "policies.yaml",
    }
    environment = {
        "CLASH_RELAY_SUBSCRIPTIONS": json.dumps(
            {
                f"SUBSCRIPTION_{index}_URL": f"https://fixture.invalid/{index}"
                for index in range(1, 6)
            }
        )
    }

    def fetch_subscription(url: str, **_kwargs) -> str:
        index = int(url.rsplit("/", 1)[1])
        return (
            "proxies:\n"
            f"  - name: Fixture {index} US 1x\n"
            "    type: http\n"
            f"    server: source-{index}.invalid.example\n"
            f"    port: {22000 + index}\n"
        )

    def fetch_rule(url: str, **_kwargs) -> str:
        name = url.rsplit("/", 1)[1].removesuffix(".list")
        return f"DOMAIN-SUFFIX,{name.lower()}.fixture.invalid\n"

    project = load_project(**paths)
    result = build_candidate(
        **paths,
        env=environment,
        fetcher=fetch_subscription,
        rule_fetcher=fetch_rule,
    )
    return project, result.config


def test_compiled_download_paths_exclude_subscription_1(repo_root: Path) -> None:
    project, candidate = _candidate(repo_root)
    report = audit_production_candidate(project, candidate)

    assert candidate["rules"][0] == "IN-NAME,download-in,下载流量"
    assert candidate["listeners"] == [
        {
            "name": "download-in",
            "type": "mixed",
            "listen": "127.0.0.1",
            "port": 7891,
            "proxy": "下载流量",
        }
    ]
    assert report["download_routing"]["runtime_graph_check"] == "passed"
    assert report["download_routing"]["subscription_1_reachable"] is False
    assert report["download_routing"]["terminal_guards"] == "passed"
    assert candidate["rules"][1] == "IN-NAME,download-in,REJECT"
    generic_rule = "RULE-SET,acl4ssr_proxy_lite,代理选择"
    assert generic_rule in candidate["rules"]
    graph = RuntimeGraph.from_candidate(candidate)
    provider_sources, proxy_sources = _runtime_source_maps(
        graph, known_source_ids={f"subscription_{index}" for index in range(1, 6)}
    )
    assert "subscription_1" not in graph.reachable_sources(
        "代理选择", proxy_sources=proxy_sources, provider_sources=provider_sources
    )
    assert "subscription_1" in graph.reachable_sources(
        "网页浏览", proxy_sources=proxy_sources, provider_sources=provider_sources
    )


def test_download_rule_order_fails_when_ai_precedes_download(repo_root: Path) -> None:
    project, candidate = _candidate(repo_root)
    changed = copy.deepcopy(candidate)
    rules = changed["rules"]
    download = rules.pop(rules.index("RULE-SET,acl4ssr_download,下载流量"))
    rules.insert(rules.index("RULE-SET,acl4ssr_ai,人工智能") + 1, download)

    with pytest.raises(ValidationError, match="ACL4SSR Download must precede AI"):
        audit_download_rule_order(project, changed)


def test_download_inbound_cannot_be_preempted(repo_root: Path) -> None:
    project, candidate = _candidate(repo_root)
    changed = copy.deepcopy(candidate)
    changed["rules"].insert(0, "DOMAIN-SUFFIX,example.com,网页浏览")

    with pytest.raises(ValidationError, match="first emitted rule"):
        audit_download_rule_order(project, changed)


def test_download_selector_cannot_reach_browsing_inventory(repo_root: Path) -> None:
    project, candidate = _candidate(repo_root)
    changed = copy.deepcopy(candidate)
    group = next(row for row in changed["proxy-groups"] if row["name"] == "下载流量")
    group["proxies"].insert(1, "网页浏览")

    with pytest.raises(ValidationError, match=r"source-use boundary|subscription_1"):
        audit_production_candidate(project, changed)


def test_download_domain_cannot_be_broadened_silently(repo_root: Path) -> None:
    project, _candidate_config = _candidate(repo_root)
    manifest = copy.deepcopy(project.acl4ssr)
    assert isinstance(manifest, dict)
    row = next(item for item in manifest["inline_rules"] if item["id"] == "download_play_gvt1")
    row["value"] = "google.com"

    with pytest.raises(ConfigurationError, match="drifted from its review"):
        audit_download_declarations(replace(project, acl4ssr=manifest))


@pytest.mark.parametrize("target", ["网页浏览", "人工智能"])
def test_download_selector_rejects_restricted_members(repo_root: Path, target: str) -> None:
    project, candidate = _candidate(repo_root)
    changed = copy.deepcopy(candidate)
    group = next(row for row in changed["proxy-groups"] if row["name"] == "下载流量")
    group["proxies"].insert(1, target)
    with pytest.raises(ValidationError, match=r"source-use boundary|subscription_1"):
        audit_production_candidate(project, changed)


@pytest.mark.parametrize("target", [None, "网页浏览", "人工智能"])
def test_download_listener_requires_bound_download_target(
    repo_root: Path, target: str | None
) -> None:
    project, candidate = _candidate(repo_root)
    changed = copy.deepcopy(candidate)
    if target is None:
        del changed["listeners"][0]["proxy"]
    else:
        changed["listeners"][0]["proxy"] = target
    with pytest.raises(ValidationError, match="download-only listener"):
        audit_download_rule_order(project, changed)


def test_download_terminal_guard_must_remain_adjacent(repo_root: Path) -> None:
    project, candidate = _candidate(repo_root)
    for replacement in (None, "网页浏览"):
        changed = copy.deepcopy(candidate)
        guard = "RULE-SET,acl4ssr_download,REJECT"
        index = changed["rules"].index(guard)
        if replacement is None:
            changed["rules"].pop(index)
        else:
            changed["rules"][index] = f"RULE-SET,acl4ssr_download,{replacement}"
        with pytest.raises(ValidationError, match="terminal guard"):
            audit_download_rule_order(project, changed)


def test_runtime_global_mode_does_not_pass_download_classifier_audit(repo_root: Path) -> None:
    project, candidate = _candidate(repo_root)
    changed = copy.deepcopy(candidate)
    changed["mode"] = "global"
    with pytest.raises(ValidationError, match="requires rule mode"):
        audit_download_rule_order(project, changed)


def test_canonical_download_inbound_must_remain_enabled(repo_root: Path) -> None:
    project, _candidate_config = _candidate(repo_root)
    config = copy.deepcopy(project.config)
    config["runtime"]["download_inbound"]["enabled"] = False
    with pytest.raises(ConfigurationError, match="enabled download inbound"):
        audit_download_declarations(replace(project, config=config))


def test_general_provider_cannot_contain_subscription_1(repo_root: Path) -> None:
    project, candidate = _candidate(repo_root)
    changed = copy.deepcopy(candidate)
    source_1 = next(
        proxy
        for provider in changed["proxy-providers"].values()
        for proxy in provider["payload"]
        if "sub_1/" in proxy["name"]
    )
    changed["proxy-providers"]["cr_general_any"]["payload"].append(copy.deepcopy(source_1))
    with pytest.raises(ValidationError, match="source-use boundary"):
        audit_production_candidate(project, changed)


def test_download_auto_cannot_use_browsing_provider(repo_root: Path) -> None:
    project, candidate = _candidate(repo_root)
    changed = copy.deepcopy(candidate)
    group = next(row for row in changed["proxy-groups"] if row["name"] == "下载自动")
    group.setdefault("use", []).append("cr_browsing_us")
    with pytest.raises(ValidationError, match=r"reachability boundary|subscription_1"):
        audit_production_candidate(project, changed)


def test_general_dialer_proxy_cannot_reach_subscription_1(repo_root: Path) -> None:
    project, candidate = _candidate(repo_root)
    changed = copy.deepcopy(candidate)
    source_1_name = next(
        proxy["name"]
        for provider in changed["proxy-providers"].values()
        for proxy in provider["payload"]
        if "sub_1/" in proxy["name"]
    )
    changed["proxy-providers"]["cr_general_any"]["payload"][0]["dialer-proxy"] = source_1_name
    with pytest.raises(ValidationError, match=r"reachability boundary|subscription_1"):
        audit_production_candidate(project, changed)


def test_unattributed_proxy_leaf_cannot_hide_in_download_selector(repo_root: Path) -> None:
    project, candidate = _candidate(repo_root)
    changed = copy.deepcopy(candidate)
    changed["proxies"] = [
        {"name": "unattributed", "type": "http", "server": "fixture.invalid", "port": 28888}
    ]
    group = next(row for row in changed["proxy-groups"] if row["name"] == "下载流量")
    group["proxies"].insert(1, "unattributed")
    with pytest.raises(ValidationError, match="unattributed runtime proxy"):
        audit_production_candidate(project, changed)
