from __future__ import annotations

import copy

import pytest

from clash_relay.ai_service_qualification import apply_ai_service_qualification
from clash_relay.errors import ValidationError

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
_PROBES = {
    "ai_openai": {
        "url": "https://chatgpt.com/",
        "method": "HEAD",
        "expected_status": "200-399",
        "interval": 3600,
        "timeout": 5000,
        "lazy": False,
        "tolerance": 50,
    },
    "ai_claude": {
        "url": "https://claude.ai/",
        "method": "HEAD",
        "expected_status": "200-399",
        "interval": 3700,
        "timeout": 5100,
        "lazy": False,
        "tolerance": 60,
    },
    "ai_gemini": {
        "url": "https://gemini.google.com/",
        "method": "HEAD",
        "expected_status": "200-399",
        "interval": 3800,
        "timeout": 5200,
        "lazy": True,
        "tolerance": 70,
    },
}


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


def test_ai_service_runtime_anchors_use_their_own_probe_declarations() -> None:
    config = _config()
    report = apply_ai_service_qualification(
        config,
        {
            "ai_openai": {"sg-openai", "us-openai"},
            "ai_claude": {"us-claude"},
            "ai_gemini": {"sg-gemini"},
        },
        copy.deepcopy(_PROBES),
    )

    groups = {group["name"]: group for group in config["proxy-groups"]}
    openai_target = groups["__CR_AI_SERVICE_OPENAI"]
    assert openai_target["type"] == "fallback"
    assert openai_target["url"] == _PROBES["ai_openai"]["url"]
    assert openai_target["expected-status"] == _PROBES["ai_openai"]["expected_status"]
    assert openai_target["interval"] == _PROBES["ai_openai"]["interval"]
    assert openai_target["timeout"] == _PROBES["ai_openai"]["timeout"]
    assert openai_target["lazy"] is False

    for child_name in openai_target["proxies"]:
        child = groups[child_name]
        assert child["url"] == _PROBES["ai_openai"]["url"]
        assert child["expected-status"] == _PROBES["ai_openai"]["expected_status"]
        assert child["interval"] == _PROBES["ai_openai"]["interval"]
        assert child["timeout"] == _PROBES["ai_openai"]["timeout"]
        assert child["lazy"] is False
        assert child["tolerance"] == _PROBES["ai_openai"]["tolerance"]

    claude_anchor = groups["__CR_AI_SERVICE_CLAUDE"]["proxies"][0]
    gemini_anchor = groups["__CR_AI_SERVICE_GEMINI"]["proxies"][0]
    assert groups[claude_anchor]["url"] == _PROBES["ai_claude"]["url"]
    assert groups[claude_anchor]["interval"] == _PROBES["ai_claude"]["interval"]
    assert groups[claude_anchor]["timeout"] == _PROBES["ai_claude"]["timeout"]
    assert groups[claude_anchor]["tolerance"] == _PROBES["ai_claude"]["tolerance"]
    assert groups[gemini_anchor]["url"] == _PROBES["ai_gemini"]["url"]
    assert groups[gemini_anchor]["lazy"] is True
    assert groups[gemini_anchor]["tolerance"] == _PROBES["ai_gemini"]["tolerance"]

    original_sg = groups["__CR_AUTO_AI_SG_SG"]
    assert original_sg["url"] == "https://www.gstatic.com/generate_204"
    assert report["service_runtime_probes"] is True


def test_ai_service_runtime_probe_declarations_fail_closed_when_incomplete() -> None:
    config = _config()
    probes = copy.deepcopy(_PROBES)
    probes.pop("ai_gemini")

    with pytest.raises(ValidationError, match="runtime service probes are missing"):
        apply_ai_service_qualification(
            config,
            {
                "ai_openai": {"sg-openai"},
                "ai_claude": {"us-claude"},
                "ai_gemini": {"sg-gemini"},
            },
            probes,
        )


def test_ai_service_runtime_probe_declarations_require_https_and_head() -> None:
    config = _config()
    probes = copy.deepcopy(_PROBES)
    probes["ai_openai"]["url"] = "http://chatgpt.com/"

    with pytest.raises(ValidationError, match="must use HTTPS"):
        apply_ai_service_qualification(
            config,
            {
                "ai_openai": {"sg-openai"},
                "ai_claude": {"us-claude"},
                "ai_gemini": {"sg-gemini"},
            },
            probes,
        )
