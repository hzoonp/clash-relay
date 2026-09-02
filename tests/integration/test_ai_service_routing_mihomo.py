from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from clash_relay.ai_runtime_reliability import (
    RUNTIME_PROVIDER_PREFIX,
    apply_openai_client_path_hardening,
)
from clash_relay.ai_service_qualification import apply_ai_service_qualification

pytestmark = pytest.mark.integration

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


def _binary() -> Path:
    value = os.environ.get("MIHOMO_BIN")
    if not value:
        pytest.skip("MIHOMO_BIN is not set")
    return Path(value).resolve()


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


def test_service_qualified_client_path_candidate_is_accepted_by_real_mihomo(
    tmp_path: Path,
) -> None:
    candidate = {
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

    apply_ai_service_qualification(
        candidate,
        {
            "ai_openai": {"sg-openai"},
            "ai_claude": {"us-claude"},
            "ai_gemini": {"sg-gemini"},
        },
    )
    report = apply_openai_client_path_hardening(candidate)
    groups = {group["name"]: group for group in candidate["proxy-groups"]}
    assert groups["人工智能"].get("hidden", False) is False
    assert groups["AI · 新加坡"]["hidden"] is True
    assert groups["AI · 新加坡"]["use"] == ["cr_ai_sg_sg"]
    assert groups["AI · 美国"]["hidden"] is True
    assert groups["AI · 美国"]["use"] == ["cr_ai_us_us"]

    openai_target = groups["__CR_AI_SERVICE_OPENAI"]
    assert openai_target["type"] == "fallback"
    assert openai_target["expected-status"] == "200-399/400-499"
    assert openai_target["max-failed-times"] == 2
    assert report["runtime_regions"] == 1
    assert report["runtime_nodes"] == 1
    runtime_providers = [
        name for name in candidate["proxy-providers"] if name.startswith(RUNTIME_PROVIDER_PREFIX)
    ]
    assert len(runtime_providers) == 1
    assert candidate["proxy-providers"][runtime_providers[0]]["health-check"] == {
        "enable": True,
        "url": "https://android.chat.openai.com/",
        "interval": 120,
        "timeout": 5000,
        "lazy": False,
        "expected-status": "200-399/400-499",
    }

    path = tmp_path / "qualified.yaml"
    path.write_text(
        yaml.safe_dump(candidate, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )

    result = subprocess.run(
        [str(_binary()), "-t", "-d", str(tmp_path), "-f", str(path)],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout
