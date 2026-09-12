from __future__ import annotations

from pathlib import Path

import yaml

from clash_relay.classify import classify_proxy, deduplicate_nodes
from clash_relay.models import SubscriptionSpec
from clash_relay.node_policy import (
    filter_proxies_by_multiplier,
    filter_proxies_by_name_patterns,
)
from clash_relay.selector import select_nodes


def _spec(
    source_id: str,
    *,
    ingest_order: int,
    uses: set[str],
    capabilities: set[str],
) -> SubscriptionSpec:
    return SubscriptionSpec(
        id=source_id,
        display_name=source_id,
        enabled=True,
        required=False,
        secret_name=f"{source_id.upper()}_URL",
        ingest_order=ingest_order,
        on_error="skip",
        allowed_uses=frozenset(uses),
        allowed_countries=frozenset({"*"}),
        default_capabilities=frozenset(capabilities),
        default_cost_level="standard",
    )


def _policies() -> dict:
    return {
        "country_classification": {
            "default": "OTHER",
            "aliases": {
                "US": [r"(?i)\bUS\b"],
                "JP": [r"(?i)\bJP\b"],
            },
        }
    }


def _proxy(name: str, *, server: str = "shared.invalid.example") -> dict:
    return {
        "name": name,
        "type": "http",
        "server": server,
        "port": 443,
    }


def test_canonical_subscription_1_declaration_is_the_restricted_boundary(
    repo_root: Path,
) -> None:
    document = yaml.safe_load((repo_root / "subscriptions.yaml").read_text(encoding="utf-8"))
    restricted = document["subscriptions"][0]

    assert restricted["id"] == "subscription_1"
    assert set(restricted["allowed_uses"]) == {"browsing", "ai"}
    assert "general" not in restricted["allowed_uses"]
    assert restricted["max_node_multiplier"] == 2.0
    assert restricted["deny_name_patterns"] == ["(?i)emby"]


def test_admission_filters_restricted_source_before_classification_and_selection() -> None:
    rows = [
        _proxy("US Standard"),
        _proxy("US Exactly 2x", server="two.invalid.example"),
        _proxy("US 2.01x", server="over.invalid.example"),
        _proxy("US EMBY 1x", server="emby.invalid.example"),
    ]
    rows, denied = filter_proxies_by_name_patterns(rows, deny_patterns=["(?i)emby"])
    rows, expensive = filter_proxies_by_multiplier(rows, max_multiplier=2.0)

    assert denied == 1
    assert expensive == 1
    assert [row["name"] for row in rows] == ["US Standard", "US Exactly 2x"]

    restricted = _spec(
        "subscription_1",
        ingest_order=100,
        uses={"browsing", "ai"},
        capabilities={"general"},
    )
    nodes = [classify_proxy(row, restricted, _policies()) for row in rows]

    assert len(select_nodes(nodes, {"source_use": "browsing"}, "US")) == 2
    assert select_nodes(nodes, {"source_use": "general"}, "US") == []


def test_duplicate_identity_never_merges_source_permission_with_other_occurrence_capability() -> (
    None
):
    restricted = _spec(
        "subscription_1",
        ingest_order=100,
        uses={"browsing", "ai"},
        capabilities={"ai"},
    )
    general = _spec(
        "subscription_2",
        ingest_order=200,
        uses={"general", "browsing", "ai"},
        capabilities={"general"},
    )
    policies = _policies()

    restricted_node = classify_proxy(_proxy("US Restricted"), restricted, policies)
    general_node = classify_proxy(_proxy("US General"), general, policies)
    deduped, duplicates = deduplicate_nodes(
        [restricted_node, general_node],
        "keep_first",
    )

    assert duplicates == 1
    assert len(deduped) == 1
    assert {item.source_id for item in deduped[0].occurrences} == {
        "subscription_1",
        "subscription_2",
    }

    # This would be selected only if the implementation illegally combined the
    # general permission from subscription_2 with the AI capability from
    # subscription_1 into one synthetic occurrence.
    forbidden = select_nodes(
        deduped,
        {"source_use": "general", "capabilities_all": ["ai"]},
        "US",
    )
    assert forbidden == []

    ai = select_nodes(
        deduped,
        {"source_use": "ai", "capabilities_all": ["ai"]},
        "US",
    )
    assert len(ai) == 1
    assert ai[0].source_id == "subscription_1"

    general_selected = select_nodes(
        deduped,
        {"source_use": "general", "capabilities_all": ["general"]},
        "US",
    )
    assert len(general_selected) == 1
    assert general_selected[0].source_id == "subscription_2"


def test_region_filter_cannot_borrow_a_different_occurrence_country() -> None:
    us = _spec(
        "subscription_1",
        ingest_order=100,
        uses={"browsing"},
        capabilities={"general"},
    )
    jp = _spec(
        "subscription_2",
        ingest_order=200,
        uses={"general"},
        capabilities={"general"},
    )
    policies = _policies()
    us_proxy = _proxy("US Shared")
    jp_proxy = _proxy("JP Shared")
    # Fingerprints ignore names, so these two source occurrences deduplicate to
    # one physical identity while retaining their source-specific countries.
    deduped, _ = deduplicate_nodes(
        [
            classify_proxy(us_proxy, us, policies),
            classify_proxy(jp_proxy, jp, policies),
        ],
        "keep_first",
    )

    assert select_nodes(deduped, {"source_use": "general"}, "US") == []
    selected = select_nodes(deduped, {"source_use": "general"}, "JP")
    assert len(selected) == 1
    assert selected[0].source_id == "subscription_2"
    assert selected[0].country == "JP"
