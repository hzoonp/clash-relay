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
from clash_relay.production_audit import audit_production_candidate


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
        {"name": "download-in", "type": "mixed", "listen": "127.0.0.1", "port": 7891}
    ]
    assert report["download_routing"]["runtime_graph_check"] == "passed"
    assert report["download_routing"]["subscription_1_reachable"] is False


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
