from __future__ import annotations

import copy

import pytest
import yaml

from clash_relay.errors import ConfigurationError
from clash_relay.routing_policy_v2 import load_routing_policy_v2


def _policies(repo_root) -> dict:
    return yaml.safe_load((repo_root / "policies.yaml").read_text(encoding="utf-8"))


def test_canonical_routing_v2_policy_is_explicit_and_safe(repo_root) -> None:
    policy = load_routing_policy_v2(_policies(repo_root))

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


def test_routing_v2_defaults_preserve_safe_legacy_contract() -> None:
    policy = load_routing_policy_v2({})

    assert policy.declared is False
    assert policy.scenario_use("browsing") == "browsing"
    assert policy.scenario_use("ai") == "ai"
    assert policy.scenario_use("media") == "general"
    assert policy.scenario_use("download") == "general"
    assert policy.ai.excluded_regions == ("HK",)
    assert policy.download.mode == "direct"


def test_routing_v2_rejects_ai_region_reenable(repo_root) -> None:
    policies = copy.deepcopy(_policies(repo_root))
    policies["routing"]["ai"]["preferred_regions"].append("HK")

    with pytest.raises(ConfigurationError, match="cannot include excluded"):
        load_routing_policy_v2(policies)


def test_routing_v2_requires_contract_exclusion(repo_root) -> None:
    policies = copy.deepcopy(_policies(repo_root))
    policies["routing"]["ai"]["excluded_regions"] = ["MO"]

    with pytest.raises(ConfigurationError, match="contract-required excluded regions: HK"):
        load_routing_policy_v2(policies)


def test_routing_v2_rejects_unknown_download_mode(repo_root) -> None:
    policies = copy.deepcopy(_policies(repo_root))
    policies["routing"]["download"]["mode"] = "browsing"

    with pytest.raises(ConfigurationError, match="download mode"):
        load_routing_policy_v2(policies)
