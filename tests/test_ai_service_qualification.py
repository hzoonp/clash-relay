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
            "cr_ai_us_us": _provider("us-claude"),
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


def test_service_qualification_routes_each_service_through_its_own_nodes() -> None:
    config = _config()
    report = apply_ai_service_qualification(
        config,
        {
            "ai_openai": {"sg-openai"},
            "ai_claude": {"us-claude"},
            "ai_gemini": {"sg-gemini"},
        },
    )

    assert [proxy["name"] for proxy in config["proxy-providers"]["cr_ai_sg_sg"]["payload"]] == [
        "sg-openai",
        "sg-gemini",
    ]
    assert [proxy["name"] for proxy in config["proxy-providers"]["cr_ai_us_us"]["payload"]] == [
        "us-claude"
    ]

    groups = {group["name"]: group for group in config["proxy-groups"]}
    openai_anchor = groups["__CR_AI_SERVICE_OPENAI"]["proxies"][0]
    claude_anchor = groups["__CR_AI_SERVICE_CLAUDE"]["proxies"][0]
    gemini_anchor = groups["__CR_AI_SERVICE_GEMINI"]["proxies"][0]
    assert openai_anchor.startswith("__CR_AI_OPENAI_")
    assert claude_anchor.startswith("__CR_AI_CLAUDE_")
    assert gemini_anchor.startswith("__CR_AI_GEMINI_")
    assert groups[openai_anchor]["filter"] == "^(sg-openai)$"
    assert groups[claude_anchor]["filter"] == "^(us-claude)$"
    assert groups[gemini_anchor]["filter"] == "^(sg-gemini)$"

    country_names = {"AI · 新加坡", "AI · 美国"}
    assert groups["人工智能"].get("hidden", False) is False
    assert groups["人工智能"]["proxies"] == ["AI · 新加坡", "AI · 美国", "DIRECT"]
    assert groups["AI · 新加坡"]["use"] == ["cr_ai_sg_sg"]
    assert groups["AI · 美国"]["use"] == ["cr_ai_us_us"]
    assert all(groups[name]["hidden"] is True for name in country_names)
    visible_groups = {
        group["name"] for group in config["proxy-groups"] if not group.get("hidden", False)
    }
    assert country_names.isdisjoint(visible_groups)
    parents = [
        group["name"]
        for group in config["proxy-groups"]
        if isinstance(group.get("proxies"), list)
        and country_names.intersection(str(item) for item in group["proxies"])
    ]
    assert parents == ["人工智能"]

    ai_rule_index = config["rules"].index("RULE-SET,acl4ssr_ai,人工智能")
    assert config["rules"][:ai_rule_index] == [
        "RULE-SET,acl4ssr_openai,__CR_AI_SERVICE_OPENAI",
        "RULE-SET,cr_ai_rules_claude,__CR_AI_SERVICE_CLAUDE",
        "RULE-SET,cr_ai_rules_gemini,__CR_AI_SERVICE_GEMINI",
    ]
    assert config["rule-providers"]["cr_ai_rules_claude"]["payload"] == sorted(_CLAUDE_RULES)
    assert config["rule-providers"]["cr_ai_rules_gemini"]["payload"] == sorted(_GEMINI_RULES)

    assert report["qualification_mode"] == "per-service"
    assert report["qualified_nodes"] == 3
    assert report["service_qualified_nodes"] == {"openai": 1, "claude": 1, "gemini": 1}
    assert report["service_fail_closed"] == []
    assert "sg-openai" not in repr(report)
    assert "sg-gemini" not in repr(report)
    assert "us-claude" not in repr(report)


def test_service_qualification_fails_closed_only_for_empty_service() -> None:
    config = _config()
    report = apply_ai_service_qualification(
        config,
        {
            "ai_openai": set(),
            "ai_claude": {"us-claude"},
            "ai_gemini": {"sg-gemini"},
        },
    )

    groups = {group["name"]: group for group in config["proxy-groups"]}
    assert groups["__CR_AI_SERVICE_OPENAI"] == {
        "name": "__CR_AI_SERVICE_OPENAI",
        "type": "select",
        "hidden": True,
        "proxies": ["REJECT"],
    }
    assert groups["AI · 新加坡"]["hidden"] is True
    assert groups["AI · 美国"]["hidden"] is True
    assert report["service_fail_closed"] == ["openai"]
    assert report["qualified_nodes"] == 2


def test_service_qualification_rejects_nested_hidden_provider_scope_drift() -> None:
    config = _config()
    country = next(
        group for group in config["proxy-groups"] if group.get("name") == "AI · 新加坡"
    )
    country["use"] = ["cr_ai_us_us"]

    with pytest.raises(ValidationError, match="exposes providers outside its routing anchor"):
        apply_ai_service_qualification(
            config,
            {
                "ai_openai": {"sg-openai"},
                "ai_claude": {"us-claude"},
                "ai_gemini": {"sg-gemini"},
            },
        )


def test_service_qualification_rejects_when_every_service_is_empty() -> None:
    config = copy.deepcopy(_config())
    with pytest.raises(ValidationError, match="no nodes passed any AI service qualification probe"):
        apply_ai_service_qualification(
            config,
            {
                "ai_openai": set(),
                "ai_claude": set(),
                "ai_gemini": set(),
            },
        )


def test_service_qualification_rejects_unknown_probe_results() -> None:
    config = _config()
    with pytest.raises(ValidationError, match="unknown candidate nodes"):
        apply_ai_service_qualification(
            config,
            {
                "ai_openai": {"not-a-runtime-node"},
                "ai_claude": {"us-claude"},
                "ai_gemini": {"sg-gemini"},
            },
        )


def test_service_qualification_detects_pinned_ai_rule_drift() -> None:
    config = _config()
    config["rule-providers"]["acl4ssr_ai"]["payload"].remove("DOMAIN-SUFFIX,claude.ai")

    with pytest.raises(ValidationError, match="service routing requires review"):
        apply_ai_service_qualification(
            config,
            {
                "ai_openai": {"sg-openai"},
                "ai_claude": {"us-claude"},
                "ai_gemini": {"sg-gemini"},
            },
        )
