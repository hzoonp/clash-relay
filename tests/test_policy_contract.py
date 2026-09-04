from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from clash_relay.errors import ConfigurationError
from clash_relay.policy_contract import load_policy_contract
from clash_relay.policy_document import load_policy_document
from clash_relay.routing_policy_v2 import load_routing_policy_v2

ROOT = Path(__file__).resolve().parents[1]


def _policies() -> dict:
    return load_policy_document(ROOT / "policies.yaml").document


def test_canonical_policy_declares_runtime_contract() -> None:
    contract = load_policy_contract(_policies())
    assert contract.declared is True
    assert contract.public_group("browsing") == "网页浏览"
    assert contract.public_group("ai") == "人工智能"
    assert contract.automatic_group("media") == "媒体自动"
    assert contract.ai.required_excluded_regions == ("HK",)
    assert contract.binding_target("proxy_lite") == "网页浏览"
    assert contract.ai.region_for_display("AI · 美国") == "US"
    assert contract.ai.region_for_display("AI · US") == "US"


def test_ai_exclusion_is_contract_driven_not_hardcoded_to_hk() -> None:
    policies = deepcopy(_policies())
    policies["routing"]["contract"]["ai"]["required_excluded_regions"] = ["SG"]
    policies["routing"]["ai"]["excluded_regions"] = ["SG"]
    policies["routing"]["ai"]["preferred_regions"] = ["US", "JP"]
    policy = load_routing_policy_v2(policies)
    assert policy.ai.excluded_regions == ("SG",)


def test_contract_rejects_missing_public_scenario_name() -> None:
    policies = deepcopy(_policies())
    del policies["routing"]["contract"]["public_groups"]["download"]
    with pytest.raises(ConfigurationError, match="public_groups"):
        load_policy_contract(policies)


def test_declared_routing_requires_an_explicit_contract() -> None:
    policies = deepcopy(_policies())
    del policies["routing"]["contract"]
    with pytest.raises(ConfigurationError, match=r"routing\.contract is required"):
        load_policy_contract(policies)


def test_project_without_routing_fails_closed() -> None:
    with pytest.raises(ConfigurationError, match="routing policy"):
        load_policy_contract({})


def test_ai_region_aliases_must_be_globally_unique() -> None:
    policies = deepcopy(_policies())
    policies["routing"]["contract"]["ai"]["region_display_names"]["SG"].append("AI · US")
    with pytest.raises(ConfigurationError, match="globally unique"):
        load_policy_contract(policies)
