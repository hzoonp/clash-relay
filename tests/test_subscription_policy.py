from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from clash_relay.builder import build_candidate
from clash_relay.errors import GenerationError
from clash_relay.node_policy import filter_proxies_by_multiplier, node_name_multiplier
from clash_relay.routing_policy import apply_acl4ssr_source_exclusions


def _proxy(name: str) -> dict:
    return {"name": name, "type": "http", "server": "example.invalid", "port": 443}


def _routing_output(*, include_secondary: bool = True) -> dict:
    payload = [
        _proxy("[GENERAL:ANY] subscription_1/HK 1x #1111111111"),
    ]
    if include_secondary:
        payload.append(_proxy("[GENERAL:ANY] subscription_2/JP 1x #2222222222"))
    return {
        "proxy-providers": {
            "cr_general_any": {
                "type": "inline",
                "payload": payload,
            }
        },
        "proxy-groups": [
            {
                "name": "__CR_AUTO_GENERAL_ANY",
                "type": "url-test",
                "hidden": True,
                "use": ["cr_general_any"],
            },
            {
                "name": "节点选择",
                "type": "select",
                "proxies": ["__CR_AUTO_GENERAL_ANY"],
            },
            {
                "name": "流媒体",
                "type": "select",
                "proxies": ["节点选择", "DIRECT"],
            },
        ],
        "rules": [
            "RULE-SET,acl4ssr_telegram,节点选择",
            "MATCH,节点选择",
        ],
    }


def test_node_name_multiplier_recognizes_common_explicit_markers() -> None:
    assert node_name_multiplier("香港 2x") == 2.0
    assert node_name_multiplier("日本 x2.5") == 2.5
    assert node_name_multiplier("美国 3倍") == 3.0
    assert node_name_multiplier("新加坡 倍率: 4") == 4.0
    assert node_name_multiplier("普通节点") is None


def test_multiplier_filter_keeps_two_times_and_unmarked_nodes() -> None:
    proxies = [
        _proxy("HK 1x"),
        _proxy("JP 2x"),
        _proxy("SG 2.01x"),
        _proxy("US 倍率 3"),
        _proxy("Unmarked"),
    ]
    kept, rejected = filter_proxies_by_multiplier(proxies, max_multiplier=2.0)

    assert [item["name"] for item in kept] == ["HK 1x", "JP 2x", "Unmarked"]
    assert rejected == 2


def test_builder_applies_multiplier_ceiling_before_provider_generation(
    project_factory, fixture_env, yaml_editor
) -> None:
    _root, paths = project_factory()

    def configure_subscriptions(document):
        for item in document["subscriptions"]:
            item["enabled"] = item["id"] == "primary"
            if item["id"] == "primary":
                item["max_node_multiplier"] = 2.0

    def configure_modules(document):
        for module in document["modules"]:
            document["modules"][module] = module == "general"

    yaml_editor(paths["subscriptions_path"], configure_subscriptions)
    yaml_editor(paths["config_path"], configure_modules)

    source = """proxies:
  - name: Keep 2x
    type: http
    server: keep.invalid.example
    port: 21001
  - name: Drop 3x
    type: http
    server: drop.invalid.example
    port: 21002
"""
    result = build_candidate(
        **paths,
        env=fixture_env,
        fetcher=lambda _url, **_kwargs: source,
    )

    payload = result.config["proxy-providers"]["cr_general_any"]["payload"]
    runtime_names = [item["name"] for item in payload]
    assert any("Keep 2x" in name for name in runtime_names)
    assert all("Drop 3x" not in name for name in runtime_names)
    primary_report = next(
        item for item in result.report["subscriptions"] if item["id"] == "primary"
    )
    assert primary_report["filtered_over_multiplier"] == 1
    assert result.report["multiplier_filtered_nodes"] == 1


def test_source_exclusion_reuses_provider_and_filters_runtime_source_prefix() -> None:
    output = _routing_output()
    report = apply_acl4ssr_source_exclusions(
        output,
        group_specs=[
            {
                "id": "policy_streaming",
                "display_name": "流媒体",
                "excluded_sources": ["subscription_1"],
            }
        ],
        known_source_ids={"subscription_1", "subscription_2"},
    )

    groups = {item["name"]: item for item in output["proxy-groups"]}
    streaming = groups["流媒体"]
    filtered_anchor = streaming["proxies"][0]
    assert filtered_anchor.startswith("__CR_AUTO_FILTER_")
    assert streaming["proxies"][1] == "DIRECT"
    assert groups[filtered_anchor]["use"] == ["cr_general_any"]
    pattern = re.compile(groups[filtered_anchor]["exclude-filter"])
    assert pattern.search("[GENERAL:ANY] subscription_1/HK 1x #1111111111")
    assert not pattern.search("[GENERAL:ANY] subscription_2/JP 1x #2222222222")
    assert set(output["proxy-providers"]) == {"cr_general_any"}
    assert report == {"group:流媒体": ["subscription_1"]}


def test_rule_and_final_exclusions_share_filtered_anchor() -> None:
    output = _routing_output()
    report = apply_acl4ssr_source_exclusions(
        output,
        group_specs=[],
        known_source_ids={"subscription_1", "subscription_2"},
        rule_specs=[
            {
                "source_id": "telegram",
                "target": "节点选择",
                "provider": "acl4ssr_telegram",
                "excluded_sources": ["subscription_1"],
            }
        ],
        final_target="节点选择",
        final_excluded_sources=["subscription_1"],
    )

    telegram_target = output["rules"][0].rsplit(",", 1)[1]
    final_target = output["rules"][1].split(",", 1)[1]
    assert telegram_target == final_target
    assert telegram_target.startswith("__CR_AUTO_FILTER_")
    assert report == {
        "rule:telegram": ["subscription_1"],
        "final": ["subscription_1"],
    }


def test_source_exclusion_fails_closed_when_only_excluded_source_survives() -> None:
    output = _routing_output(include_secondary=False)
    apply_acl4ssr_source_exclusions(
        output,
        group_specs=[
            {
                "id": "policy_streaming",
                "display_name": "流媒体",
                "excluded_sources": ["subscription_1"],
            }
        ],
        known_source_ids={"subscription_1"},
    )

    groups = {item["name"]: item for item in output["proxy-groups"]}
    anchor = groups["流媒体"]["proxies"][0]
    assert anchor.startswith("__CR_FAIL_CLOSED_FILTER_")
    assert groups[anchor]["proxies"] == ["REJECT"]


def test_source_exclusion_rejects_unknown_subscription_ids() -> None:
    with pytest.raises(GenerationError, match="unknown subscription sources"):
        apply_acl4ssr_source_exclusions(
            _routing_output(),
            group_specs=[
                {
                    "id": "policy_streaming",
                    "display_name": "流媒体",
                    "excluded_sources": ["missing_source"],
                }
            ],
            known_source_ids={"subscription_1", "subscription_2"},
        )


def test_canonical_subscription_policy_does_not_override_acl4ssr_routing(repo_root: Path) -> None:
    subscriptions = yaml.safe_load((repo_root / "subscriptions.yaml").read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in subscriptions["subscriptions"]}
    subscription_1 = by_id["subscription_1"]
    assert subscription_1["max_node_multiplier"] == 2.0
    assert set(subscription_1["allowed_uses"]) == {"general", "ai"}

    acl4ssr = yaml.safe_load((repo_root / "rules/acl4ssr.yaml").read_text(encoding="utf-8"))
    assert "final_excluded_sources" not in acl4ssr
    assert all("excluded_sources" not in group for group in acl4ssr["groups"])
    assert all("excluded_sources" not in source for source in acl4ssr["sources"])
