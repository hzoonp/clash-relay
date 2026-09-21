from __future__ import annotations

import pytest

from clash_relay.dns_policy import apply_dns_routing_policy
from clash_relay.errors import GenerationError


def _config(mode: str = "acl4ssr") -> dict:
    return {
        "runtime": {
            "dns": {
                "routing_policy": mode,
                "nameservers": ["https://1.1.1.1/dns-query"],
                "direct_nameservers": ["https://dns.alidns.com/dns-query"],
            }
        }
    }


def test_dns_policy_reuses_routing_scenarios_and_rule_providers() -> None:
    output = {
        "dns": {"enable": True},
        "rule-providers": {
            "acl4ssr_china_domain": {"type": "inline"},
            "acl4ssr_openai": {"type": "inline"},
        },
    }
    rules = [
        {"provider": "acl4ssr_china_domain", "scenario": "direct"},
        {"provider": "acl4ssr_openai", "scenario": "ai"},
        {"rule": {"type": "GEOIP", "value": "CN"}, "scenario": "direct"},
    ]

    report = apply_dns_routing_policy(output, config=_config(), external_rules=rules)

    assert report == {
        "status": "compiled",
        "mode": "acl4ssr",
        "direct_rulesets": 1,
        "proxy_rulesets": 1,
        "total_rulesets": 2,
    }
    assert output["dns"]["nameserver-policy"] == {
        "rule-set:acl4ssr_china_domain": ["https://dns.alidns.com/dns-query"],
        "rule-set:acl4ssr_openai": ["https://1.1.1.1/dns-query"],
    }


def test_dns_policy_fails_closed_when_provider_scenario_is_missing() -> None:
    output = {
        "dns": {"enable": True},
        "rule-providers": {"acl4ssr_unknown": {"type": "inline"}},
    }

    with pytest.raises(GenerationError, match="scenario metadata"):
        apply_dns_routing_policy(
            output,
            config=_config(),
            external_rules=[{"provider": "acl4ssr_unknown"}],
        )


def test_dns_policy_none_leaves_dns_unmodified() -> None:
    output = {"dns": {"enable": True}}
    report = apply_dns_routing_policy(output, config=_config("none"), external_rules=[])

    assert report == {"status": "not_applicable", "mode": "none"}
    assert "nameserver-policy" not in output["dns"]
