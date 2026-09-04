from __future__ import annotations

import copy
from pathlib import Path

import pytest

from clash_relay.errors import ConfigurationError
from clash_relay.policy_document import load_policy_document
from clash_relay.routing_policy_v2 import load_routing_policy_v2

ROOT = Path(__file__).resolve().parents[1]


def _policies() -> dict:
    return load_policy_document(ROOT / "policies.yaml").document


def test_canonical_routing_v2_policy_is_explicit_and_safe() -> None:
    policy = load_routing_policy_v2(_policies())

    assert policy.declared is True
    assert policy.scenario_use("direct") == "general"
    assert policy.scenario_use("general") == "general"
    assert policy.scenario_use("browsing") == "browsing"
    assert policy.scenario_use("media") == "general"
    assert policy.scenario_use("download") == "general"
    assert policy.scenario_use("ai") == "ai"
    assert policy.scenario_use("final") == "general"
    assert policy.ai.excluded_regions == ("HK",)
    assert policy.ai.preferred_regions == ("US", "SG", "JP", "TW", "KR", "OTHER")
    assert "HK" not in policy.ai.preferred_regions
    assert policy.download.mode == "general_auto"


def test_routing_v2_without_declaration_fails_closed() -> None:
    with pytest.raises(ConfigurationError, match="routing policy is required"):
        load_routing_policy_v2({})


def test_routing_v2_rejects_ai_region_reenable() -> None:
    policies = copy.deepcopy(_policies())
    policies["routing"]["ai"]["preferred_regions"].append("HK")

    with pytest.raises(ConfigurationError, match="cannot include excluded"):
        load_routing_policy_v2(policies)


def test_routing_v2_requires_contract_exclusion() -> None:
    policies = copy.deepcopy(_policies())
    policies["routing"]["ai"]["excluded_regions"] = ["MO"]

    with pytest.raises(ConfigurationError, match="contract-required excluded regions: HK"):
        load_routing_policy_v2(policies)


def test_routing_v2_rejects_unknown_download_mode() -> None:
    policies = copy.deepcopy(_policies())
    policies["routing"]["download"]["mode"] = "browsing"

    with pytest.raises(ConfigurationError, match="download mode"):
        load_routing_policy_v2(policies)
