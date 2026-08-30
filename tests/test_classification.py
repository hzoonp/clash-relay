from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from clash_relay.classify import classify_proxy, deduplicate_nodes
from clash_relay.errors import GenerationError
from clash_relay.models import SubscriptionSpec
from clash_relay.selector import select_nodes


def _spec(**overrides) -> SubscriptionSpec:  # noqa: ANN003
    values = {
        "id": "source",
        "display_name": "Source",
        "enabled": True,
        "required": True,
        "secret_name": "SUB_SOURCE",
        "priority": 100,
        "on_error": "fail",
        "allowed_uses": frozenset({"general", "ai", "bulk", "residential"}),
        "allowed_countries": frozenset({"US", "JP", "SG", "OTHER"}),
        "default_capabilities": frozenset({"general"}),
        "default_cost_level": "standard",
        "node_metadata": {},
        "name_rules": (),
    }
    values.update(overrides)
    return SubscriptionSpec(**values)


def _proxy(name: str = "Node") -> dict:
    return {
        "name": name,
        "type": "http",
        "server": f"{name.lower().replace(' ', '-')}.invalid.example",
        "port": 443,
    }


@pytest.fixture(scope="module")
def policies(repo_root: Path) -> dict:
    return yaml.safe_load((repo_root / "policies.yaml").read_text(encoding="utf-8"))


def test_country_name_classifier_is_auxiliary(policies: dict) -> None:
    node = classify_proxy(_proxy("Fast JP Tokyo"), _spec(), policies)
    assert node.country == "JP"


def test_exact_country_metadata_overrides_name_classifier(policies: dict) -> None:
    spec = _spec(node_metadata={"JP in name": {"country": "US"}})
    node = classify_proxy(_proxy("JP in name"), spec, policies)
    assert node.country == "US"


def test_name_rule_can_add_nonrestricted_capability(policies: dict) -> None:
    spec = _spec(name_rules=({"pattern": "AI", "add_capabilities": ["ai"]},))
    node = classify_proxy(_proxy("AI Node"), spec, policies)
    assert node.capabilities == frozenset({"general", "ai"})


def test_exact_metadata_can_add_restricted_capability(policies: dict) -> None:
    spec = _spec(
        node_metadata={"Home": {"country": "US", "add_capabilities": ["residential"]}}
    )
    node = classify_proxy(_proxy("Home"), spec, policies)
    assert "residential" in node.capabilities


def test_metadata_removes_default_capability(policies: dict) -> None:
    spec = _spec(node_metadata={"Special": {"remove_capabilities": ["general"]}})
    node = classify_proxy(_proxy("Special"), spec, policies)
    assert "general" not in node.capabilities


def test_metadata_cost_overrides_default(policies: dict) -> None:
    spec = _spec(node_metadata={"Cheap": {"cost_level": "low"}})
    assert classify_proxy(_proxy("Cheap"), spec, policies).cost_level == "low"


def test_default_country_and_capability(policies: dict) -> None:
    node = classify_proxy(_proxy("Unclassified"), _spec(), policies)
    assert node.country == "OTHER"
    assert node.capabilities == frozenset({"general"})


def test_node_can_have_multiple_capabilities(policies: dict) -> None:
    spec = _spec(
        node_metadata={
            "Multi": {"add_capabilities": ["ai", "bulk"], "country": "SG"}
        }
    )
    node = classify_proxy(_proxy("Multi"), spec, policies)
    assert node.capabilities == frozenset({"general", "ai", "bulk"})


def test_selection_requires_source_use(policies: dict) -> None:
    node = classify_proxy(_proxy(), _spec(allowed_uses=frozenset({"general"})), policies)
    selector = {
        "source_use": "ai",
        "capabilities_any": [],
        "capabilities_all": ["general"],
        "excluded_capabilities": [],
        "allowed_cost_levels": ["standard"],
    }
    assert select_nodes([node], selector, "ANY") == []


def test_selection_requires_country_allowed_by_source(policies: dict) -> None:
    spec = _spec(
        allowed_countries=frozenset({"JP"}),
        node_metadata={"US": {"country": "US"}},
    )
    node = classify_proxy(_proxy("US"), spec, policies)
    selector = {
        "source_use": "general",
        "capabilities_any": ["general"],
        "capabilities_all": [],
        "excluded_capabilities": [],
        "allowed_cost_levels": ["standard"],
    }
    assert select_nodes([node], selector, "ANY") == []


def test_selection_any_all_excluded_and_cost(policies: dict) -> None:
    spec = _spec(
        node_metadata={
            "Eligible": {"add_capabilities": ["bulk"], "cost_level": "low"},
            "Excluded": {
                "add_capabilities": ["bulk", "residential"],
                "cost_level": "low",
            },
        }
    )
    eligible = classify_proxy(_proxy("Eligible"), spec, policies)
    excluded = classify_proxy(_proxy("Excluded"), spec, policies)
    selector = {
        "source_use": "bulk",
        "capabilities_any": ["general", "bulk"],
        "capabilities_all": ["bulk"],
        "excluded_capabilities": ["residential"],
        "allowed_cost_levels": ["low"],
    }
    assert select_nodes([eligible, excluded], selector, "ANY") == [eligible]


def test_region_filter(policies: dict) -> None:
    spec = _spec(node_metadata={"US": {"country": "US"}, "JP": {"country": "JP"}})
    us = classify_proxy(_proxy("US"), spec, policies)
    jp = classify_proxy(_proxy("JP"), spec, policies)
    selector = {
        "source_use": "general",
        "capabilities_any": ["general"],
        "capabilities_all": [],
        "excluded_capabilities": [],
        "allowed_cost_levels": ["standard"],
    }
    assert select_nodes([jp, us], selector, "US") == [us]


def test_priority_only_controls_deterministic_order_not_filtering(policies: dict) -> None:
    first = classify_proxy(_proxy("First"), _spec(id="a", priority=999), policies)
    second = classify_proxy(_proxy("Second"), _spec(id="b", priority=1), policies)
    selector = {
        "source_use": "general",
        "capabilities_any": ["general"],
        "capabilities_all": [],
        "excluded_capabilities": [],
        "allowed_cost_levels": ["standard"],
    }
    selected = select_nodes([first, second], selector, "ANY")
    assert {node.source_id for node in selected} == {"a", "b"}
    assert selected[0].source_id == "b"


def test_deduplication_keeps_lowest_priority_number(policies: dict) -> None:
    proxy = _proxy("Same")
    slow = classify_proxy(proxy, _spec(id="slow", priority=200), policies)
    preferred = classify_proxy(proxy, _spec(id="preferred", priority=100), policies)
    nodes, removed = deduplicate_nodes([slow, preferred], "keep_first")
    assert removed == 1
    assert nodes[0].source_id == "preferred"


def test_deduplication_error_policy(policies: dict) -> None:
    node = classify_proxy(_proxy("Same"), _spec(), policies)
    with pytest.raises(GenerationError, match="duplicate"):
        deduplicate_nodes([node, replace(node, source_id="other")], "error")


def test_fingerprint_ignores_node_name(policies: dict) -> None:
    one = classify_proxy(_proxy("One"), _spec(), policies)
    renamed_proxy = _proxy("Two")
    renamed_proxy["server"] = one.proxy["server"]
    two = classify_proxy(renamed_proxy, _spec(), policies)
    assert one.fingerprint == two.fingerprint
