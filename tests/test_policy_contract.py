from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from clash_relay.errors import ConfigurationError
from clash_relay.policy_contract import load_policy_contract
from clash_relay.routing_policy_v2 import load_routing_policy_v2
from clash_relay.util import load_yaml_file

ROOT = Path(__file__).resolve().parents[1]


def test_canonical_policy_declares_runtime_contract() -> None:
    policies = load_yaml_file(ROOT / "policies.yaml")
    contract = load_policy_contract(policies)
    assert contract.declared is True
    assert contract.public_groups["browsing"] == "网页浏览"
    assert contract.public_groups["ai"] == "人工智能"
    assert contract.ai.required_excluded_regions == ("HK",)
    assert contract.binding_targets["proxy_lite"] == "网页浏览"


def test_ai_exclusion_is_contract_driven_not_hardcoded_to_hk() -> None:
    policies = deepcopy(load_yaml_file(ROOT / "policies.yaml"))
    policies["routing"]["contract"]["ai"]["required_excluded_regions"] = ["SG"]
    policies["routing"]["ai"]["excluded_regions"] = ["SG"]
    policies["routing"]["ai"]["preferred_regions"] = ["US", "JP"]
    policy = load_routing_policy_v2(policies)
    assert policy.ai.excluded_regions == ("SG",)


def test_contract_rejects_missing_public_scenario_name() -> None:
    policies = deepcopy(load_yaml_file(ROOT / "policies.yaml"))
    del policies["routing"]["contract"]["public_groups"]["download"]
    with pytest.raises(ConfigurationError, match="public_groups"):
        load_policy_contract(policies)
