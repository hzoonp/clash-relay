from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from clash_relay.acl4ssr import load_acl4ssr_rules
from clash_relay.ai_service_qualification import apply_ai_service_qualification
from clash_relay.builder import build_candidate
from clash_relay.mihomo import validate_with_mihomo
from clash_relay.util import dump_yaml

_EXPECTED_COMPATIBILITY_OMISSIONS = {
    "proxy_media": (1, "Clash/Providers/ProxyMedia.yaml"),
    "download": (7, "Clash/Providers/Download.yaml"),
}

_EXPECTED_PUBLIC_SCENARIOS = {
    "代理选择",
    "网页浏览",
    "人工智能",
    "流媒体",
    "消息通讯",
    "下载流量",
}
_EXPECTED_HIDDEN_ROUTING_GROUPS = {
    "自动选择",
    "手动切换",
    "网页自动",
    "香港节点",
    "台湾节点",
    "新加坡节点",
    "日本节点",
    "美国节点",
    "韩国节点",
    "媒体自动",
    "通讯自动",
    "下载自动",
    "全球直连",
    "广告拦截",
    "谷歌FCM",
    "微软服务",
    "苹果服务",
    "漏网之鱼",
}
_EXPECTED_AI_COUNTRY_GROUPS = {
    "AI · 新加坡",
    "AI · 日本",
    "AI · 美国",
    "AI · 台湾",
    "AI · 韩国",
    "AI · 其他地区",
}
_PUBLIC_GENERAL_CHOICES = [
    "香港节点",
    "台湾节点",
    "新加坡节点",
    "日本节点",
    "美国节点",
    "韩国节点",
    "DIRECT",
]
_ACL_COMPATIBILITY_SELECTORS = {
    "全球直连": ["DIRECT", "代理选择", "自动选择"],
    "广告拦截": ["REJECT", "DIRECT"],
    "谷歌FCM": ["代理选择", "全球直连", "自动选择"],
    "微软服务": ["全球直连", "代理选择"],
    "苹果服务": ["代理选择", "全球直连"],
    "漏网之鱼": ["代理选择", "全球直连", "自动选择"],
}
_REMOVED_PRE_P10_GROUPS = {
    "微软Bing",
    "微软云盘",
    "电报消息",
    "网易音乐",
    "游戏平台",
    "油管视频",
    "奈飞节点",
    "奈飞视频",
    "巴哈姆特",
    "哔哩哔哩",
    "国内媒体",
    "国外媒体",
}


def _assert_canonical_compatibility_report(report: dict) -> None:
    assert report["unverified_legacy_rules"] == 0
    assert report["verified_compatibility_omissions"] == 8
    actual = {
        source["id"]: (
            source["verified_compatibility_omissions"],
            source["mihomo_compatibility_path"],
        )
        for source in report["sources"]
        if source["verified_compatibility_omissions"]
    }
    assert actual == _EXPECTED_COMPATIBILITY_OMISSIONS


@pytest.mark.integration
def test_pinned_acl4ssr_profile_validates_with_real_mihomo(
    project_factory,
    fixture_env,
    yaml_editor,
) -> None:
    root, paths = project_factory()

    def enable_acl4ssr(document):
        document["rule_sources"] = {"acl4ssr": {"enabled": True, "manifest": "rules/acl4ssr.yaml"}}
        document["modules"].update(
            {
                "general": True,
                "chatgpt": False,
                "claude": False,
                "gemini": False,
                "google_play": False,
                "bulk": False,
            }
        )

    yaml_editor(paths["config_path"], enable_acl4ssr)
    result = build_candidate(**paths, env=fixture_env)
    acl_report = result.report["rule_sources"]["acl4ssr"]
    groups = {item["name"]: item for item in result.config["proxy-groups"]}
    rule_providers = result.config["rule-providers"]

    assert acl_report["repository"] == "ACL4SSR/ACL4SSR"
    assert len(acl_report["ref"]) == 40
    assert acl_report["rules"] > 1000
    assert acl_report["rule_providers"] == len(rule_providers)
    assert rule_providers
    assert all(item["type"] == "inline" for item in rule_providers.values())
    assert all(item["behavior"] == "classical" for item in rule_providers.values())
    assert all("url" not in item and "path" not in item for item in rule_providers.values())
    assert any(rule.startswith("RULE-SET,acl4ssr_ai,AI") for rule in result.config["rules"])
    assert any(
        rule.startswith("RULE-SET,acl4ssr_youtube,YouTube") for rule in result.config["rules"]
    )
    assert "GEOIP,CN,Direct,no-resolve" in result.config["rules"]
    assert result.config["rules"][-1] == "MATCH,Final"
    assert len(result.config["rules"]) < acl_report["rules"]
    assert groups["Proxy"]["proxies"] == ["__CR_AUTO_GENERAL_ANY"]
    assert groups["Auto"]["proxies"] == ["__CR_AUTO_GENERAL_ANY"]
    assert "__CR_SERVICE_FALLBACK_GENERAL" not in groups
    assert groups["Direct"]["proxies"] == ["DIRECT", "Proxy", "Auto"]
    assert groups["Block"]["proxies"] == ["REJECT", "DIRECT"]
    assert groups["AI"]["proxies"] == ["Proxy", "Auto", "Direct"]
    assert groups["Microsoft"]["proxies"] == ["Direct", "Proxy", "Auto"]
    assert groups["Apple"]["proxies"] == ["Direct", "Proxy", "Auto"]
    assert groups["Telegram"]["proxies"] == ["Proxy", "Auto", "Direct"]
    assert groups["YouTube"]["proxies"] == ["Proxy", "Auto", "Direct"]
    assert groups["Netflix"]["proxies"] == ["Proxy", "Auto", "Direct"]
    assert groups["Final"]["proxies"] == ["Proxy", "Auto", "Direct"]
    assert "ChatGPT" not in groups
    assert "Claude" not in groups
    assert "Gemini" not in groups
    assert "Google Play" not in groups
    assert "Video & Downloads" not in groups
    assert "use" not in groups["Auto"]

    candidate = root / ".acl4ssr-candidate.yaml"
    candidate.write_text(result.yaml_text, encoding="utf-8")
    validation = validate_with_mihomo(
        Path(os.environ["MIHOMO_BIN"]),
        candidate,
        secret_values=result.secret_values,
    )
    assert validation["config_test"] == "passed"
    assert validation["startup_smoke"] == "passed"


@pytest.mark.integration
def test_canonical_strict_acl4ssr_profile_validates_with_real_mihomo(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    secret_file = tmp_path / "subscriptions.yaml"
    secret_file.write_text(
        yaml.safe_dump(
            {
                f"SUBSCRIPTION_{index}_URL": f"https://fixture.invalid/subscription-{index}"
                for index in range(1, 5)
            },
            allow_unicode=True,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    def fake_subscription(url: str, **_kwargs) -> str:
        source_number = int(url.rsplit("-", 1)[1])
        base_port = 20000 + source_number * 100
        names = ["香港", "台湾", "新加坡", "日本", "美国", "韩国", "其他"]
        proxies = [
            {
                "name": f"{name} Fixture {source_number}",
                "type": "http",
                "server": "example.com",
                "port": base_port + offset,
            }
            for offset, name in enumerate(names, start=1)
        ]
        return yaml.safe_dump({"proxies": proxies}, allow_unicode=True, sort_keys=False)

    result = build_candidate(
        config_path=repo_root / "config.yaml",
        subscriptions_path=repo_root / "subscriptions.yaml",
        services_path=repo_root / "services.yaml",
        policies_path=repo_root / "policies.yaml",
        secret_file=secret_file,
        env={},
        fetcher=fake_subscription,
    )

    ai_names = {
        str(proxy["name"])
        for provider_name, provider in result.config["proxy-providers"].items()
        if str(provider_name).startswith("cr_ai_")
        for proxy in provider["payload"]
    }
    assert all("香港" not in name for name in ai_names)
    apply_ai_service_qualification(
        result.config,
        {
            "ai_openai": set(ai_names),
            "ai_claude": set(ai_names),
            "ai_gemini": set(ai_names),
        },
    )

    groups = {item["name"]: item for item in result.config["proxy-groups"]}
    visible = {
        item["name"] for item in result.config["proxy-groups"] if not item.get("hidden", False)
    }
    assert visible == _EXPECTED_PUBLIC_SCENARIOS
    assert {"国内服务", "更多策略"}.isdisjoint(groups)
    assert groups["代理选择"]["proxies"] == [
        "自动选择",
        "香港节点",
        "台湾节点",
        "新加坡节点",
        "日本节点",
        "美国节点",
        "韩国节点",
        "手动切换",
        "DIRECT",
    ]
    assert groups["流媒体"]["proxies"] == ["媒体自动", *_PUBLIC_GENERAL_CHOICES]
    assert groups["消息通讯"]["proxies"] == ["通讯自动", *_PUBLIC_GENERAL_CHOICES]
    assert groups["下载流量"]["proxies"] == ["下载自动", *_PUBLIC_GENERAL_CHOICES]
    for public_name in ("流媒体", "消息通讯", "下载流量"):
        assert groups[public_name]["type"] == "select"
        assert "use" not in groups[public_name]
        assert "filter" not in groups[public_name]

    assert "应用净化" not in groups
    assert _REMOVED_PRE_P10_GROUPS.isdisjoint(groups)
    for name, expected in _ACL_COMPATIBILITY_SELECTORS.items():
        assert groups[name]["type"] == "select"
        assert groups[name]["hidden"] is True
        assert groups[name]["proxies"] == expected
        assert "use" not in groups[name]
        assert "filter" not in groups[name]

    assert all(groups[name]["hidden"] is True for name in _EXPECTED_HIDDEN_ROUTING_GROUPS)
    assert all(groups[name]["hidden"] is True for name in _EXPECTED_AI_COUNTRY_GROUPS)
    assert "AI · 香港" not in groups
    assert result.config["rules"][-1] == "MATCH,漏网之鱼"
    assert "GEOIP,CN,全球直连,no-resolve" in result.config["rules"]
    assert "RULE-SET,acl4ssr_telegram,消息通讯" in result.config["rules"]
    assert "RULE-SET,acl4ssr_proxy_media,流媒体" in result.config["rules"]
    assert "RULE-SET,acl4ssr_download,下载流量" in result.config["rules"]
    assert "RULE-SET,acl4ssr_proxy_lite,网页浏览" in result.config["rules"]
    assert all("acl4ssr_proxy_gfwlist" not in rule for rule in result.config["rules"])
    assert all("acl4ssr_youtube" not in rule for rule in result.config["rules"])
    assert all("acl4ssr_netflix" not in rule for rule in result.config["rules"])

    rules = result.config["rules"]
    assert rules.index("RULE-SET,acl4ssr_telegram,消息通讯") < rules.index(
        "RULE-SET,acl4ssr_proxy_media,流媒体"
    )
    assert rules.index("RULE-SET,acl4ssr_proxy_media,流媒体") < rules.index(
        "RULE-SET,acl4ssr_download,下载流量"
    )
    assert rules.index("RULE-SET,acl4ssr_download,下载流量") < rules.index(
        "RULE-SET,acl4ssr_proxy_lite,网页浏览"
    )
    assert rules.index("RULE-SET,acl4ssr_proxy_lite,网页浏览") < rules.index(
        "RULE-SET,acl4ssr_china_domain,全球直连"
    )

    policy_rule_targets = {
        rule.split(",")[1] if rule.startswith("MATCH,") else rule.split(",")[2]
        for rule in result.config["rules"]
        if "__CR_AI_SERVICE_" not in rule
    } - {"DIRECT", "REJECT"}
    assert policy_rule_targets <= set(groups)
    for target in policy_rule_targets - visible:
        assert groups[target]["hidden"] is True

    _assert_canonical_compatibility_report(result.report["rule_sources"]["acl4ssr"])
    assert "source_exclusions" not in result.report

    candidate = tmp_path / "canonical-strict.yaml"
    candidate.write_text(dump_yaml(result.config), encoding="utf-8")
    validation = validate_with_mihomo(
        Path(os.environ["MIHOMO_BIN"]),
        candidate,
        secret_values=result.secret_values,
    )
    assert validation["config_test"] == "passed"
    assert validation["startup_smoke"] == "passed"


@pytest.mark.integration
def test_canonical_acl4ssr_pin_has_only_verified_legacy_omissions(repo_root: Path) -> None:
    manifest = yaml.safe_load((repo_root / "rules/acl4ssr.yaml").read_text(encoding="utf-8"))
    _providers, _directives, report = load_acl4ssr_rules(
        manifest,
        modules={"general": True},
        timeout=20,
    )

    assert report is not None
    assert report["ref"] == "c498ae4911f15b19c5ceaef6f8737ca8705b4430"
    _assert_canonical_compatibility_report(report)
