from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

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
    assert report == {"流媒体": ["subscription_1"]}


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


def test_canonical_subscription_1_policy_is_locked(repo_root: Path) -> None:
    subscriptions = yaml.safe_load((repo_root / "subscriptions.yaml").read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in subscriptions["subscriptions"]}
    subscription_1 = by_id["subscription_1"]
    assert subscription_1["max_node_multiplier"] == 2.0
    assert set(subscription_1["allowed_uses"]) == {"general", "ai"}

    acl4ssr = yaml.safe_load((repo_root / "rules/acl4ssr.yaml").read_text(encoding="utf-8"))
    groups = {item["display_name"]: item for item in acl4ssr["groups"]}
    assert groups["流媒体"]["excluded_sources"] == ["subscription_1"]
    assert groups["国内服务"]["excluded_sources"] == ["subscription_1"]
    assert "excluded_sources" not in groups["人工智能"]
