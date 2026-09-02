from __future__ import annotations

import copy

from clash_relay.ai_runtime_reliability import (
    RUNTIME_PROVIDER_PREFIX,
    apply_openai_client_path_hardening,
    audit_openai_client_path,
    runtime_health_contract,
)
from clash_relay.ai_service_qualification import apply_ai_service_qualification

_CLAUDE_RULES = [
    "DOMAIN-KEYWORD,anthropic",
    "DOMAIN-KEYWORD,claude",
    "DOMAIN-SUFFIX,anthropic.com",
    "DOMAIN-SUFFIX,claude.ai",
    "DOMAIN-SUFFIX,claude.com",
    "DOMAIN-SUFFIX,claudeusercontent.com",
]
_GEMINI_RULES = [
    "DOMAIN,ai.google.dev",
    "DOMAIN,aistudio.google.com",
    "DOMAIN,bard.google.com",
    "DOMAIN,gemini.google.com",
    "DOMAIN,generativelanguage.googleapis.com",
    "DOMAIN,notebooklm.google.com",
    "DOMAIN-SUFFIX,bard.google.com",
    "DOMAIN-SUFFIX,gemini.google.com",
    "DOMAIN-SUFFIX,makersuite.google.com",
    "DOMAIN-SUFFIX,notebooklm.google",
    "DOMAIN-SUFFIX,notebooklm.google.com",
]


def _provider(*names: str) -> dict:
    return {
        "type": "inline",
        "health-check": {
            "enable": True,
            "url": "https://www.gstatic.com/generate_204",
            "interval": 300,
            "timeout": 5000,
            "lazy": True,
            "expected-status": "204",
        },
        "payload": [
            {
                "name": name,
                "type": "http",
                "server": f"{index}.invalid.example",
                "port": 443,
            }
            for index, name in enumerate(names, start=1)
        ],
    }


def _auto(name: str, provider: str) -> dict:
    return {
        "name": name,
        "type": "url-test",
        "hidden": True,
        "use": [provider],
        "url": "https://www.gstatic.com/generate_204",
        "interval": 300,
        "timeout": 5000,
        "lazy": True,
        "expected-status": "204",
        "tolerance": 50,
    }


def _config() -> dict:
    return {
        "mixed-port": 7890,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "warning",
        "proxy-providers": {
            "cr_ai_sg_sg": _provider("sg-openai", "sg-gemini"),
            "cr_ai_us_us": _provider("us-openai", "us-claude"),
        },
        "rule-providers": {
            "acl4ssr_ai": {
                "type": "inline",
                "behavior": "classical",
                "payload": [*_CLAUDE_RULES, *_GEMINI_RULES, "DOMAIN-SUFFIX,perplexity.ai"],
            },
            "acl4ssr_openai": {
                "type": "inline",
                "behavior": "classical",
                "payload": ["DOMAIN-KEYWORD,openai", "DOMAIN-SUFFIX,chatgpt.com"],
            },
        },
        "proxy-groups": [
            _auto("__CR_AUTO_AI_SG_SG", "cr_ai_sg_sg"),
            {
                "name": "AI · 新加坡",
                "type": "select",
                "proxies": ["__CR_AUTO_AI_SG_SG"],
                "use": ["cr_ai_sg_sg"],
            },
            _auto("__CR_AUTO_AI_US_US", "cr_ai_us_us"),
            {
                "name": "AI · 美国",
                "type": "select",
                "proxies": ["__CR_AUTO_AI_US_US"],
                "use": ["cr_ai_us_us"],
            },
            {
                "name": "人工智能",
                "type": "select",
                "proxies": ["AI · 新加坡", "AI · 美国", "DIRECT"],
            },
        ],
        "rules": [
            "RULE-SET,acl4ssr_ai,人工智能",
            "RULE-SET,acl4ssr_openai,人工智能",
            "MATCH,人工智能",
        ],
    }


def _service_qualified() -> dict:
    config = _config()
    apply_ai_service_qualification(
        config,
        {
            "ai_openai": {"sg-openai", "us-openai"},
            "ai_claude": {"us-claude"},
            "ai_gemini": {"sg-gemini"},
        },
        preferred_regions=("US", "SG"),
    )
    return config


def test_openai_runtime_uses_client_local_android_health_checks_and_stable_fallback() -> None:
    config = _service_qualified()
    original_sg_health = copy.deepcopy(config["proxy-providers"]["cr_ai_sg_sg"]["health-check"])
    original_us_health = copy.deepcopy(config["proxy-providers"]["cr_ai_us_us"]["health-check"])

    report = apply_openai_client_path_hardening(config)

    groups = {group["name"]: group for group in config["proxy-groups"]}
    target = groups["__CR_AI_SERVICE_OPENAI"]
    contract = runtime_health_contract()
    assert target["type"] == "fallback"
    assert target["url"] == "https://android.chat.openai.com/"
    assert target["interval"] == 120
    assert target["timeout"] == 5000
    assert target["lazy"] is False
    assert target["expected-status"] == "200-499"
    assert target["max-failed-times"] == 2
    assert target["proxies"][0].startswith("__CR_AI_OPENAI_")
    assert len(target["proxies"]) == 2

    runtime_provider_names: set[str] = set()
    for anchor_name in target["proxies"]:
        anchor = groups[anchor_name]
        assert anchor["type"] == "fallback"
        assert anchor["hidden"] is True
        assert "filter" not in anchor
        assert "url" not in anchor
        assert len(anchor["use"]) == 1
        provider_name = anchor["use"][0]
        assert provider_name.startswith(RUNTIME_PROVIDER_PREFIX)
        runtime_provider_names.add(provider_name)
        provider = config["proxy-providers"][provider_name]
        assert provider["health-check"] == {
            "enable": True,
            "url": contract["url"],
            "interval": contract["interval"],
            "timeout": contract["timeout"],
            "lazy": contract["lazy"],
            "expected-status": contract["expected-status"],
        }
        assert provider["payload"]
        assert all(" [OAI:" in proxy["name"] for proxy in provider["payload"])

    assert len(runtime_provider_names) == 2
    assert config["proxy-providers"]["cr_ai_sg_sg"]["health-check"] == original_sg_health
    assert config["proxy-providers"]["cr_ai_us_us"]["health-check"] == original_us_health
    assert report["status"] == "hardened"
    assert report["selection"] == "stable_first_fallback"
    assert report["runtime_regions"] == 2
    assert report["runtime_providers"] == 2
    assert report["runtime_nodes"] == 2
    assert "sg-openai" not in repr(report)
    assert "us-openai" not in repr(report)
    assert audit_openai_client_path(config)["status"] == "passed"


def test_openai_runtime_hardening_is_idempotent() -> None:
    config = _service_qualified()
    first = apply_openai_client_path_hardening(config)
    providers_after_first = set(config["proxy-providers"])

    second = apply_openai_client_path_hardening(config)

    assert first["runtime_nodes"] == second["runtime_nodes"] == 2
    assert set(config["proxy-providers"]) == providers_after_first
    assert second["status"] == "passed"


def test_openai_runtime_preserves_service_fail_closed_state() -> None:
    config = _config()
    apply_ai_service_qualification(
        config,
        {
            "ai_openai": set(),
            "ai_claude": {"us-claude"},
            "ai_gemini": {"sg-gemini"},
        },
    )

    report = apply_openai_client_path_hardening(config)

    assert report["status"] == "fail_closed"
    assert report["runtime_nodes"] == 0
    assert not any(name.startswith(RUNTIME_PROVIDER_PREFIX) for name in config["proxy-providers"])
    assert audit_openai_client_path(config)["status"] == "fail_closed"
