from __future__ import annotations

from pathlib import Path

import yaml

from clash_relay.builder import build_candidate
from clash_relay.classify import deduplicate_nodes
from clash_relay.models import Node
from clash_relay.node_policy import filter_proxies_by_name_patterns
from clash_relay.selector import select_nodes


def _proxy(name: str, *, server: str = "shared.invalid") -> dict:
    return {"name": name, "type": "http", "server": server, "port": 443}


def _node(source_id: str, allowed_uses: set[str], fingerprint: str, priority: int) -> Node:
    name = f"{source_id} HK"
    return Node(
        source_id=source_id,
        source_display_name=source_id,
        source_priority=priority,
        source_allowed_uses=frozenset(allowed_uses),
        source_allowed_countries=frozenset({"*"}),
        original_name=name,
        proxy=_proxy(name),
        country="HK",
        capabilities=frozenset({"general"}),
        cost_level="standard",
        fingerprint=fingerprint,
    )


def _selector(source_use: str) -> dict:
    return {
        "source_use": source_use,
        "capabilities_any": ["general"],
        "capabilities_all": [],
        "excluded_capabilities": [],
        "allowed_cost_levels": ["standard"],
    }


def test_deny_name_patterns_remove_nodes_before_classification() -> None:
    proxies = [_proxy("HK EMBY 1x"), _proxy("HK normal 1x")]

    kept, rejected = filter_proxies_by_name_patterns(
        proxies,
        deny_patterns=["(?i)emby"],
    )

    assert [item["name"] for item in kept] == ["HK normal 1x"]
    assert rejected == 1


def test_dedup_preserves_later_source_general_eligibility() -> None:
    nodes = [
        _node("subscription_1", {"browsing", "ai"}, "same", 100),
        _node("subscription_2", {"general", "browsing", "ai"}, "same", 200),
    ]

    deduplicated, duplicates = deduplicate_nodes(nodes, "keep_first")

    assert duplicates == 1
    assert len(deduplicated) == 1
    assert {item.source_id for item in deduplicated[0].occurrences} == {
        "subscription_1",
        "subscription_2",
    }
    general = select_nodes(deduplicated, _selector("general"), "ANY")
    browsing = select_nodes(deduplicated, _selector("browsing"), "ANY")
    assert [item.source_id for item in general] == ["subscription_2"]
    assert [item.source_id for item in browsing] == ["subscription_1"]


def test_builder_reports_name_admission_rejections(project_factory, fixture_env, yaml_editor) -> None:
    _root, paths = project_factory()

    def configure_subscriptions(document):
        for item in document["subscriptions"]:
            item["enabled"] = item["id"] == "primary"
            if item["id"] == "primary":
                item["deny_name_patterns"] = ["(?i)emby"]

    def configure_modules(document):
        for module in document["modules"]:
            document["modules"][module] = module == "general"

    yaml_editor(paths["subscriptions_path"], configure_subscriptions)
    yaml_editor(paths["config_path"], configure_modules)

    source = """proxies:
  - name: Drop EMBY
    type: http
    server: drop.invalid.example
    port: 21001
  - name: Keep normal
    type: http
    server: keep.invalid.example
    port: 21002
"""
    result = build_candidate(
        **paths,
        env=fixture_env,
        fetcher=lambda _url, **_kwargs: source,
    )

    runtime_names = [
        item["name"] for item in result.config["proxy-providers"]["cr_general_any"]["payload"]
    ]
    assert all("EMBY" not in name for name in runtime_names)
    assert any("Keep normal" in name for name in runtime_names)
    primary_report = next(
        item for item in result.report["subscriptions"] if item["id"] == "primary"
    )
    assert primary_report["filtered_by_name"] == 1
    assert result.report["name_filtered_nodes"] == 1


def test_canonical_subscription_1_uses_true_admission_filter(repo_root: Path) -> None:
    document = yaml.safe_load((repo_root / "subscriptions.yaml").read_text(encoding="utf-8"))
    first = next(item for item in document["subscriptions"] if item["id"] == "subscription_1")

    assert first["deny_name_patterns"] == ["(?i)emby"]
    assert not any(
        "emby" in str(rule.get("pattern", "")).casefold()
        for rule in first.get("name_rules", [])
    )
