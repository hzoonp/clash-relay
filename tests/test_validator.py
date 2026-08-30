from __future__ import annotations

import copy

import pytest

from clash_relay.errors import ValidationError
from clash_relay.validator import validate_generated_config


def _candidate(built_candidate) -> dict:
    return copy.deepcopy(built_candidate.config)


def test_valid_candidate_passes(built_candidate) -> None:
    validate_generated_config(_candidate(built_candidate))


def test_flat_top_level_proxies_rejected(built_candidate) -> None:
    config = _candidate(built_candidate)
    config["proxies"] = [{"name": "raw", "type": "direct"}]
    with pytest.raises(ValidationError, match="top-level raw proxies"):
        validate_generated_config(config)


def test_empty_provider_rejected(built_candidate) -> None:
    config = _candidate(built_candidate)
    next(iter(config["proxy-providers"].values()))["payload"] = []
    with pytest.raises(ValidationError, match="empty"):
        validate_generated_config(config)


def test_remote_provider_rejected(built_candidate) -> None:
    config = _candidate(built_candidate)
    provider = next(iter(config["proxy-providers"].values()))
    provider["type"] = "http"
    provider["url"] = "https://secret.invalid/sub"
    with pytest.raises(ValidationError, match="not inline"):
        validate_generated_config(config)


def test_missing_provider_health_check_rejected(built_candidate) -> None:
    config = _candidate(built_candidate)
    next(iter(config["proxy-providers"].values())).pop("health-check")
    with pytest.raises(ValidationError, match="health-check"):
        validate_generated_config(config)


def test_invalid_provider_expected_status_rejected(built_candidate) -> None:
    config = _candidate(built_candidate)
    next(iter(config["proxy-providers"].values()))["health-check"]["expected-status"] = "999"
    with pytest.raises(ValidationError, match="expected-status"):
        validate_generated_config(config)


def test_duplicate_runtime_proxy_names_rejected(built_candidate) -> None:
    config = _candidate(built_candidate)
    providers = list(config["proxy-providers"].values())
    providers[1]["payload"][0]["name"] = providers[0]["payload"][0]["name"]
    with pytest.raises(ValidationError, match="shared by providers"):
        validate_generated_config(config)


def test_unknown_provider_use_rejected(built_candidate) -> None:
    config = _candidate(built_candidate)
    auto = next(group for group in config["proxy-groups"] if group["type"] == "url-test")
    auto["use"] = ["missing"]
    with pytest.raises(ValidationError, match="unknown provider"):
        validate_generated_config(config)


def test_unknown_group_reference_rejected(built_candidate) -> None:
    config = _candidate(built_candidate)
    fallback = next(group for group in config["proxy-groups"] if group["type"] == "fallback")
    fallback["proxies"] = ["missing"]
    with pytest.raises(ValidationError, match="unknown proxy/group"):
        validate_generated_config(config)


def test_public_group_cannot_reference_nodes_directly(built_candidate) -> None:
    config = _candidate(built_candidate)
    public = next(group for group in config["proxy-groups"] if not group.get("hidden", False))
    public["proxies"] = [next(iter(config["proxy-providers"].values()))["payload"][0]["name"]]
    with pytest.raises(ValidationError, match="SERVICE-FALLBACK"):
        validate_generated_config(config)


def test_policy_only_public_group_can_reference_valid_public_groups(built_candidate) -> None:
    config = _candidate(built_candidate)
    config["proxy-groups"].append(
        {
            "name": "Policy Only",
            "type": "select",
            "proxies": ["DIRECT", "Proxy"],
        }
    )
    validate_generated_config(config)


def test_policy_only_public_group_cannot_reference_internal_auto_group(built_candidate) -> None:
    config = _candidate(built_candidate)
    internal_auto = next(
        group["name"]
        for group in config["proxy-groups"]
        if group.get("hidden", False) and group["type"] == "url-test"
    )
    config["proxy-groups"].append(
        {
            "name": "Unsafe Policy",
            "type": "select",
            "proxies": [internal_auto],
        }
    )
    with pytest.raises(ValidationError, match="forbidden internal"):
        validate_generated_config(config)


def test_policy_only_public_group_cannot_attach_provider_use(built_candidate) -> None:
    config = _candidate(built_candidate)
    config["proxy-groups"].append(
        {
            "name": "Unsafe Provider Policy",
            "type": "select",
            "proxies": ["Proxy"],
            "use": [next(iter(config["proxy-providers"]))],
        }
    )
    with pytest.raises(ValidationError, match=r"provider-backed.*SERVICE-FALLBACK"):
        validate_generated_config(config)


def test_hidden_group_requires_reserved_prefix(built_candidate) -> None:
    config = _candidate(built_candidate)
    hidden = next(group for group in config["proxy-groups"] if group.get("hidden", False))
    hidden["name"] = "Internal But Unreserved"
    with pytest.raises(ValidationError, match="reserved __CR_"):
        validate_generated_config(config)


def test_group_cycle_rejected(built_candidate) -> None:
    config = _candidate(built_candidate)
    groups = [group for group in config["proxy-groups"] if group["type"] == "fallback"]
    first, second = groups[:2]
    first["proxies"] = [second["name"]]
    second["proxies"] = [first["name"]]
    with pytest.raises(ValidationError, match="cycle"):
        validate_generated_config(config)


def test_unknown_rule_target_rejected(built_candidate) -> None:
    config = _candidate(built_candidate)
    config["rules"].insert(-1, "DOMAIN,example.invalid,Missing")
    with pytest.raises(ValidationError, match="unknown target"):
        validate_generated_config(config)


def test_final_match_rule_required(built_candidate) -> None:
    config = _candidate(built_candidate)
    config["rules"][-1] = "DOMAIN,example.invalid,Proxy"
    with pytest.raises(ValidationError, match="final rule"):
        validate_generated_config(config)


def test_duplicate_rules_rejected(built_candidate) -> None:
    config = _candidate(built_candidate)
    config["rules"].insert(0, config["rules"][0])
    with pytest.raises(ValidationError, match="duplicates"):
        validate_generated_config(config)


def test_uncontrolled_dialer_proxy_rejected(built_candidate) -> None:
    config = _candidate(built_candidate)
    regular_name = next(
        name for name in config["proxy-providers"] if not name.startswith("cr_chain_exit_")
    )
    config["proxy-providers"][regular_name]["payload"][0]["dialer-proxy"] = (
        "__CR_CHAIN_ENTRY_AUTO_CHAIN"
    )
    with pytest.raises(ValidationError, match="uncontrolled"):
        validate_generated_config(config)


def test_chain_dialer_must_reference_existing_group(built_candidate) -> None:
    config = _candidate(built_candidate)
    exit_provider = config["proxy-providers"]["cr_chain_exit_chain"]
    exit_provider["payload"][0]["dialer-proxy"] = "__CR_CHAIN_ENTRY_AUTO_MISSING"
    with pytest.raises(ValidationError, match="unknown group"):
        validate_generated_config(config)


def test_subscription_secret_url_leak_rejected(built_candidate) -> None:
    config = _candidate(built_candidate)
    secret = "https://subscription.invalid.example/path" + "?token=top-secret"
    next(iter(config["proxy-providers"].values()))["payload"][0]["password"] = secret
    with pytest.raises(ValidationError, match="leaked"):
        validate_generated_config(config, secret_urls=(secret,))


@pytest.mark.parametrize(
    "field",
    [
        "external-controller",
        "external-controller-tls",
        "secret",
        "authentication",
        "listeners",
        "tunnels",
    ],
)
def test_private_control_top_level_fields_rejected(built_candidate, field: str) -> None:
    config = _candidate(built_candidate)
    config[field] = "not allowed"
    with pytest.raises(ValidationError, match="forbidden private/control"):
        validate_generated_config(config)
