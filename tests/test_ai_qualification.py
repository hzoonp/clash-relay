from __future__ import annotations

import copy

import pytest

from clash_relay.ai_qualification import apply_ai_qualification
from clash_relay.errors import ValidationError


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
            {"name": name, "type": "http", "server": f"{index}.invalid.example", "port": 443}
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
        "proxy-providers": {
            "cr_general_any": _provider("general-only"),
            "cr_ai_sg_sg": _provider("sg-good", "sg-bad"),
            "cr_ai_us_us": _provider("us-bad"),
        },
        "proxy-groups": [
            _auto("__CR_AUTO_GENERAL_ANY", "cr_general_any"),
            {
                "name": "节点选择",
                "type": "select",
                "proxies": ["__CR_AUTO_GENERAL_ANY"],
                "use": ["cr_general_any"],
            },
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
                "name": "__CR_FAIL_CLOSED_AI_KR",
                "type": "select",
                "hidden": True,
                "proxies": ["REJECT"],
            },
            {
                "name": "AI · 韩国",
                "type": "select",
                "proxies": ["__CR_FAIL_CLOSED_AI_KR"],
            },
            {
                "name": "人工智能",
                "type": "select",
                "proxies": ["AI · 新加坡", "AI · 美国", "AI · 韩国", "DIRECT"],
            },
        ],
    }


def test_ai_qualification_keeps_only_live_nodes_and_prunes_empty_countries() -> None:
    config = _config()
    report = apply_ai_qualification(config, {"sg-good"})

    assert [item["name"] for item in config["proxy-providers"]["cr_ai_sg_sg"]["payload"]] == [
        "sg-good"
    ]
    assert "cr_ai_us_us" not in config["proxy-providers"]
    assert config["proxy-providers"]["cr_general_any"]["payload"][0]["name"] == "general-only"

    groups = {item["name"]: item for item in config["proxy-groups"]}
    assert "AI · 新加坡" in groups
    assert "AI · 美国" not in groups
    assert "AI · 韩国" not in groups
    assert "__CR_AUTO_AI_US_US" not in groups
    assert "__CR_FAIL_CLOSED_AI_KR" not in groups
    assert groups["人工智能"]["proxies"] == ["AI · 新加坡", "DIRECT"]

    assert report == {
        "tested_nodes": 3,
        "qualified_nodes": 1,
        "country_groups": {"AI · 新加坡": 1, "AI · 美国": 0},
        "removed_country_groups": ["AI · 美国", "AI · 韩国"],
    }
    assert "sg-good" not in repr(report)


def test_ai_qualification_fails_closed_when_no_node_passes() -> None:
    config = copy.deepcopy(_config())
    with pytest.raises(ValidationError, match="no nodes passed all AI qualification probes"):
        apply_ai_qualification(config, set())
