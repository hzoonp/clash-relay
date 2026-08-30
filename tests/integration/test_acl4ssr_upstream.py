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
    "china_media": (1, "Clash/Providers/ChinaMedia.yaml"),
    "proxy_media": (1, "Clash/Providers/ProxyMedia.yaml"),
    "download": (7, "Clash/Providers/Download.yaml"),
}


def _assert_canonical_compatibility_report(report: dict) -> None:
    assert report["unverified_legacy_rules"] == 0
    assert report["verified_compatibility_omissions"] == 9
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
    assert visible == {"节点选择", "人工智能", "流媒体", "国内服务", "更多策略"}
    assert groups["节点选择"]["proxies"] == [
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
    assert groups["哔哩哔哩"]["proxies"] == ["全球直连", "台湾节点", "香港节点"]
    assert groups["哔哩哔哩"]["hidden"] is True
    assert groups["广告拦截"]["proxies"] == ["REJECT", "DIRECT"]
    assert groups["广告拦截"]["hidden"] is True
    assert result.config["rules"][-1] == "MATCH,漏网之鱼"
    assert "GEOIP,CN,全球直连,no-resolve" in result.config["rules"]
    assert "RULE-SET,acl4ssr_bilibili_hmt,哔哩哔哩" in result.config["rules"]
    assert "RULE-SET,acl4ssr_bilibili,哔哩哔哩" in result.config["rules"]
    assert "RULE-SET,acl4ssr_telegram,电报消息" in result.config["rules"]

    presentation_only = {"流媒体", "国内服务", "更多策略"}
    rule_targets = {
        rule.split(",")[1] if rule.startswith("MATCH,") else rule.split(",")[2]
        for rule in result.config["rules"]
    }
    assert presentation_only.isdisjoint(rule_targets)
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
