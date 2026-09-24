from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from clash_relay.browsing_regions import region_display_name
from clash_relay.builder import build_candidate
from clash_relay.mihomo import validate_with_mihomo
from clash_relay.runtime_graph import RuntimeGraph


def _build(repo_root, *, secondary_kr: bool):
    urls = {
        f"SUBSCRIPTION_{index}_URL": f"https://fixture.invalid/sub/{index}" for index in range(1, 6)
    }

    def fetcher(url: str, **_kwargs) -> str:
        source = int(url.rsplit("/", 1)[1])
        nodes: list[dict[str, Any]] = [
            {
                "name": f"Singapore Node S{source}",
                "type": "http",
                "server": f"sg-source-{source}.fixture.invalid",
                "port": 20000 + source,
            }
        ]
        if source == 1 or (source == 2 and secondary_kr):
            nodes.append(
                {
                    "name": f"KR Node S{source}",
                    "type": "http",
                    "server": f"kr-source-{source}.fixture.invalid",
                    "port": 21000 + source,
                }
            )
        return yaml.safe_dump({"proxies": nodes}, allow_unicode=True, sort_keys=False)

    def rule_fetcher(_url: str, **_kwargs) -> str:
        return "DOMAIN-SUFFIX,fixture.invalid\n"

    return build_candidate(
        config_path=repo_root / "config.yaml",
        subscriptions_path=repo_root / "subscriptions.yaml",
        policies_path=repo_root / "policies.yaml",
        env=urls,
        fetcher=fetcher,
        rule_fetcher=rule_fetcher,
    )


def _assert_public_references_resolve(config: dict[str, Any]) -> None:
    groups = {group["name"]: group for group in config["proxy-groups"]}
    regions = {"香港节点", "台湾节点", "新加坡节点", "日本节点", "美国节点", "韩国节点"}
    for group in config["proxy-groups"]:
        if group.get("hidden", False):
            continue
        for member in group.get("proxies", []):
            if member in regions:
                assert member in groups

    general_providers = [
        provider
        for name, provider in config["proxy-providers"].items()
        if name.startswith("cr_general_")
    ]
    assert all(
        "sub_1/" not in str(proxy.get("name", ""))
        for provider in general_providers
        for proxy in provider.get("payload", [])
    )


def test_subscription_1_korea_stays_in_browsing_but_general_region_is_omitted(repo_root) -> None:
    result = _build(repo_root, secondary_kr=False)
    groups = {group["name"]: group for group in result.config["proxy-groups"]}
    general = result.report["acl4ssr_groups"]["regional_group_counts"]["韩国节点"]

    assert region_display_name("KR") in groups
    assert "韩国节点" not in groups
    assert general == {"leaf_nodes": 0, "omitted": True}
    browsing_leafs = RuntimeGraph.from_candidate(result.config).walk_resolved("网页浏览").proxies
    assert any("[BROWSING:KR] sub_1/" in proxy for proxy in browsing_leafs)
    _assert_public_references_resolve(result.config)


def test_subscription_2_korea_restores_general_region_selector(repo_root) -> None:
    result = _build(repo_root, secondary_kr=True)
    groups = {group["name"]: group for group in result.config["proxy-groups"]}
    general = result.report["acl4ssr_groups"]["regional_group_counts"]["韩国节点"]

    assert "韩国节点" in groups
    assert general == {"leaf_nodes": 1, "omitted": False}
    general_leafs = RuntimeGraph.from_candidate(result.config).walk_resolved("韩国节点").proxies
    assert general_leafs
    assert all("sub_1/" not in proxy for proxy in general_leafs)
    _assert_public_references_resolve(result.config)


@pytest.mark.integration
@pytest.mark.parametrize("secondary_kr", [False, True])
def test_region_omission_shapes_load_in_real_mihomo(
    repo_root, tmp_path, secondary_kr: bool
) -> None:
    binary = os.environ.get("MIHOMO_BIN", "").strip()
    if not binary or not Path(binary).is_file():
        pytest.skip("MIHOMO_BIN is not set")
    result = _build(repo_root, secondary_kr=secondary_kr)
    candidate = tmp_path / "region-omission.yaml"
    candidate.write_text(result.yaml_text, encoding="utf-8")

    report = validate_with_mihomo(
        Path(binary), candidate, startup_seconds=1.0, config_test_timeout=90.0
    )

    assert report["config_test"] == "passed"
    assert report["startup_smoke"] == "passed"
