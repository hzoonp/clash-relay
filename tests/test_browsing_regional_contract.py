from __future__ import annotations

from pathlib import Path

from clash_relay.browsing_regions import DEFAULT_BROWSING_REGIONS
from clash_relay.policy_document import load_policy_document
from clash_relay.util import load_yaml_file


def test_canonical_browsing_policy_is_region_first_and_independent_from_ai(
    repo_root: Path,
) -> None:
    policies = load_policy_document(repo_root / "policies.yaml").document
    preferred = policies["routing"]["browsing"]["preferred_regions"]
    ai_preferred = policies["routing"]["ai"]["preferred_regions"]
    pool = next(item for item in policies["pools"] if item["id"] == "browsing")

    assert preferred == list(DEFAULT_BROWSING_REGIONS)
    assert pool["regions"] == preferred
    assert pool["fallback_order"] == preferred
    assert pool["source_use"] == "browsing"
    assert ai_preferred == ["US", "SG", "JP", "TW", "KR", "OTHER"]
    assert preferred != ai_preferred
    assert policies["scheduler"]["browsing"]["region_switch_interval"] == 300
    assert (
        policies["scheduler"]["browsing"]["region_switch_interval"]
        >= policies["probes"]["browsing"]["interval"]
    )


def test_canonical_country_classifier_covers_every_browsing_region(repo_root: Path) -> None:
    policies = load_policy_document(repo_root / "policies.yaml").document
    classifier = policies["country_classification"]

    assert classifier["default"] == "OTHER"
    assert set(DEFAULT_BROWSING_REGIONS) - {"OTHER"} <= set(classifier["aliases"])


def test_regional_scheduling_does_not_widen_subscription_1(repo_root: Path) -> None:
    document = load_yaml_file(repo_root / "subscriptions.yaml")
    subscription = next(
        item for item in document["subscriptions"] if item["id"] == "subscription_1"
    )

    assert subscription["secret_name"] == "SUBSCRIPTION_1_URL"
    assert set(subscription["allowed_uses"]) == {"browsing", "ai"}
    assert "general" not in subscription["allowed_uses"]
    assert subscription["max_node_multiplier"] == 2.0
