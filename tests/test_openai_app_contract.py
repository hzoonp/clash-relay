from __future__ import annotations

import copy

import pytest

from clash_relay.errors import ValidationError
from clash_relay.openai_app_contract import (
    OPENAI_SERVICE_TARGET,
    ROUTE_RULES,
    RULE_PROVIDER,
    apply_route_lock,
    audit_route_lock,
    cache_service_key,
    contract_fingerprint,
    critical_probes,
    supporting_probes,
)


def _candidate() -> dict:
    return {
        "mixed-port": 7890,
        "mode": "rule",
        "proxy-groups": [
            {
                "name": OPENAI_SERVICE_TARGET,
                "type": "select",
                "hidden": True,
                "proxies": ["REJECT"],
            }
        ],
        "rule-providers": {
            "acl4ssr_openai": {
                "type": "inline",
                "behavior": "classical",
                "payload": ["DOMAIN-SUFFIX,chatgpt.com"],
            },
            "acl4ssr_ai": {
                "type": "inline",
                "behavior": "classical",
                "payload": ["DOMAIN-SUFFIX,perplexity.ai"],
            },
        },
        "rules": [
            f"RULE-SET,acl4ssr_openai,{OPENAI_SERVICE_TARGET}",
            "RULE-SET,acl4ssr_ai,人工智能",
            "MATCH,人工智能",
        ],
    }


def test_contract_covers_android_auth_and_supporting_hosts_without_shared_suffixes() -> None:
    rules = set(ROUTE_RULES)

    assert "DOMAIN-SUFFIX,openai.com" in rules
    assert "DOMAIN-SUFFIX,chatgpt.com" in rules
    assert "DOMAIN-SUFFIX,oaistatsig.com" in rules
    assert "DOMAIN,android.chat.openai.com" in rules
    assert "DOMAIN,cdn.workos.com" in rules
    assert "DOMAIN,challenges.cloudflare.com" in rules
    assert "DOMAIN,rum.browser-intake-datadoghq.com" in rules
    assert "DOMAIN-SUFFIX,workos.com" not in rules
    assert "DOMAIN-SUFFIX,cloudflare.com" not in rules
    assert "DOMAIN-SUFFIX,stripe.com" not in rules
    assert "DOMAIN-SUFFIX,sentry.io" not in rules
    assert "DOMAIN-SUFFIX,datadoghq.com" not in rules


def test_contract_fingerprint_only_rotates_openai_cache_namespace() -> None:
    fingerprint = contract_fingerprint()

    assert len(fingerprint) == 64
    assert cache_service_key("ai_openai").startswith("ai_openai@")
    assert cache_service_key("ai_openai") != "ai_openai"
    assert cache_service_key("ai_claude") == "ai_claude"
    assert cache_service_key("ai_gemini") == "ai_gemini"


def test_critical_probe_contract_extends_primary_and_supporting_is_diagnostic() -> None:
    primary = {
        "name": "ai_openai",
        "url": "https://chatgpt.com/",
        "method": "HEAD",
        "expected_status": "200-399",
        "timeout": 5000,
    }

    critical = critical_probes(primary)
    supporting = supporting_probes()

    assert critical[0] == primary
    assert {probe["name"] for probe in critical} == {
        "ai_openai",
        "openai_app_android",
        "openai_app_auth",
        "openai_app_setup_auth",
    }
    assert len(supporting) == 4
    assert all(probe["method"] == "HEAD" for probe in (*critical, *supporting))
    assert all(str(probe["url"]).startswith("https://") for probe in (*critical, *supporting))


def test_route_lock_precedes_acl_openai_and_generic_ai() -> None:
    candidate = _candidate()

    report = apply_route_lock(candidate)

    lock = f"RULE-SET,{RULE_PROVIDER},{OPENAI_SERVICE_TARGET}"
    assert candidate["rules"][:3] == [
        lock,
        f"RULE-SET,acl4ssr_openai,{OPENAI_SERVICE_TARGET}",
        "RULE-SET,acl4ssr_ai,人工智能",
    ]
    assert candidate["rule-providers"][RULE_PROVIDER]["payload"] == list(ROUTE_RULES)
    assert report["status"] == "locked"
    assert audit_route_lock(candidate)["status"] == "passed"


def test_route_lock_is_idempotent() -> None:
    candidate = _candidate()
    apply_route_lock(candidate)
    apply_route_lock(candidate)

    lock = f"RULE-SET,{RULE_PROVIDER},{OPENAI_SERVICE_TARGET}"
    assert candidate["rules"].count(lock) == 1


def test_route_audit_rejects_escape_or_contract_drift() -> None:
    candidate = _candidate()
    apply_route_lock(candidate)
    drifted = copy.deepcopy(candidate)
    drifted["rule-providers"][RULE_PROVIDER]["payload"].append("DOMAIN-SUFFIX,workos.com")

    with pytest.raises(ValidationError, match="drifted from the reviewed contract"):
        audit_route_lock(drifted)

    bypass = copy.deepcopy(candidate)
    bypass["rules"].remove(f"RULE-SET,{RULE_PROVIDER},{OPENAI_SERVICE_TARGET}")
    with pytest.raises(ValidationError, match="one lock/OpenAI/generic-AI rule"):
        audit_route_lock(bypass)


def test_prequalification_audit_does_not_require_materialized_lock() -> None:
    candidate = _candidate()
    candidate["proxy-groups"] = []

    assert audit_route_lock(candidate)["status"] == "pre_qualification"
