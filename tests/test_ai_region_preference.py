from __future__ import annotations

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
        "payload": [{"name": name, "type": "direct"} for name in names],
    }


def _anchor(name: str, provider: str) -> dict:
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


def _wrapper(name: str, anchor: str, provider: str) -> dict:
    return {
        "name": name,
        "type": "select",
        "proxies": [anchor],
        "use": [provider],
    }


def test_openai_fallback_materializes_preferred_regions_in_order() -> None:
    candidate = {
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
                "payload": [*_CLAUDE_RULES, *_GEMINI_RULES],
            },
            "acl4ssr_openai": {
                "type": "inline",
                "behavior": "classical",
                "payload": ["DOMAIN-KEYWORD,openai", "DOMAIN-SUFFIX,chatgpt.com"],
            },
        },
        "proxy-groups": [
            _anchor("__CR_AUTO_AI_SG_SG", "cr_ai_sg_sg"),
            _wrapper("AI · 新加坡", "__CR_AUTO_AI_SG_SG", "cr_ai_sg_sg"),
            _anchor("__CR_AUTO_AI_US_US", "cr_ai_us_us"),
            _wrapper("AI · 美国", "__CR_AUTO_AI_US_US", "cr_ai_us_us"),
            {
                "name": "人工智能",
                "type": "select",
                "proxies": ["AI · 美国", "AI · 新加坡", "DIRECT"],
            },
        ],
        "rules": [
            "RULE-SET,acl4ssr_ai,人工智能",
            "RULE-SET,acl4ssr_openai,人工智能",
            "MATCH,人工智能",
        ],
    }

    report = apply_ai_service_qualification(
        candidate,
        {
            "ai_openai": {"us-openai", "sg-openai"},
            "ai_claude": {"us-claude"},
            "ai_gemini": {"sg-gemini"},
        },
        preferred_regions=("US", "SG", "JP", "TW", "KR", "OTHER"),
    )

    groups = {group["name"]: group for group in candidate["proxy-groups"]}
    openai_target = groups["__CR_AI_SERVICE_OPENAI"]
    assert openai_target["type"] == "fallback"
    providers = [groups[name]["use"] for name in openai_target["proxies"]]
    assert providers == [["cr_ai_us_us"], ["cr_ai_sg_sg"]]
    assert report["preferred_regions"] == ["US", "SG", "JP", "TW", "KR", "OTHER"]
