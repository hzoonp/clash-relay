from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from clash_relay.acl4ssr import parse_acl4ssr_list
from clash_relay.builder import build_candidate
from clash_relay.errors import ConfigurationError, GenerationError
from clash_relay.util import dump_yaml


def _group_members(group: dict) -> list[str]:
    members: list[str] = []
    for member in group["members"]:
        if "builtin" in member:
            members.append(member["builtin"])
        elif "group" in member:
            members.append(member["group"])
        else:
            members.append(f"auto_pool:{member['auto_pool']}")
    return members


def test_parse_acl4ssr_list_normalizes_supported_rules() -> None:
    rules, skipped = parse_acl4ssr_list(
        """
# comment
DOMAIN-SUFFIX,example.com
IP-CIDR,203.0.113.0/24,no-resolve
URL-REGEX,^https://example\\.com/
""",
        source_id="fixture",
    )

    assert rules == [
        {"type": "DOMAIN-SUFFIX", "value": "example.com"},
        {"type": "IP-CIDR", "value": "203.0.113.0/24", "options": ["no-resolve"]},
    ]
    assert skipped == 1


def test_parse_acl4ssr_list_rejects_unknown_rule_types() -> None:
    with pytest.raises(GenerationError, match="unsupported rule type"):
        parse_acl4ssr_list("FUTURE-RULE,example.com\n", source_id="fixture")


def test_acl4ssr_manifest_is_immutable_attributed_and_strict(repo_root: Path) -> None:
    manifest = yaml.safe_load((repo_root / "rules/acl4ssr.yaml").read_text(encoding="utf-8"))
    assert manifest["repository"] == "ACL4SSR/ACL4SSR"
    assert manifest["ref"] == "c498ae4911f15b19c5ceaef6f8737ca8705b4430"
    assert manifest["license"] == "CC-BY-SA-4.0"
    assert manifest["final_target"] == "漏网之鱼"
    assert "final_excluded_sources" not in manifest

    expected_targets = {
        "local_area_network": "全球直连",
        "unban": "全球直连",
        "ban_ad": "广告拦截",
        "ban_program_ad": "应用净化",
        "google_fcm": "谷歌FCM",
        "google_cn": "全球直连",
        "steam_cn": "全球直连",
        "bing": "微软Bing",
        "onedrive": "微软云盘",
        "microsoft": "微软服务",
        "apple": "苹果服务",
        "telegram": "电报消息",
        "ai": "人工智能",
        "openai": "人工智能",
        "netease_music": "网易音乐",
        "epic": "游戏平台",
        "origin": "游戏平台",
        "sony": "游戏平台",
        "steam": "游戏平台",
        "nintendo": "游戏平台",
        "youtube": "油管视频",
        "netflix": "奈飞视频",
        "bahamut": "巴哈姆特",
        "bilibili_hmt": "哔哩哔哩",
        "bilibili": "哔哩哔哩",
        "china_media": "国内媒体",
        "proxy_media": "国外媒体",
        "proxy_gfwlist": "节点选择",
        "china_domain": "全球直连",
        "china_company_ip": "全球直连",
        "download": "全球直连",
    }
    sources = {item["id"]: item for item in manifest["sources"]}
    assert {source_id: source["target"] for source_id, source in sources.items()} == expected_targets
    assert all("excluded_sources" not in source for source in sources.values())

    assert manifest["inline_rules"] == [
        {
            "id": "geoip_cn",
            "type": "GEOIP",
            "value": "CN",
            "target": "全球直连",
            "priority": 920,
            "module": "general",
            "options": ["no-resolve"],
        }
    ]

    groups = {item["display_name"]: item for item in manifest["groups"]}
    assert _group_members(groups["节点选择"]) == [
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
    assert _group_members(groups["哔哩哔哩"]) == ["全球直连", "台湾节点", "香港节点"]
    assert _group_members(groups["全球直连"]) == ["DIRECT", "节点选择", "自动选择"]
    assert _group_members(groups["广告拦截"]) == ["REJECT", "DIRECT"]
    assert _group_members(groups["应用净化"]) == ["REJECT", "DIRECT"]
    assert _group_members(groups["漏网之鱼"]) == [
        "节点选择",
        "自动选择",
        "DIRECT",
        "香港节点",
        "台湾节点",
        "新加坡节点",
        "日本节点",
        "美国节点",
        "韩国节点",
        "手动切换",
    ]

    assert groups["自动选择"]["provider_pool"] == "general"
    assert groups["自动选择"]["filter"] == ".*"
    assert groups["自动选择"]["url"] == "http://www.gstatic.com/generate_204"
    assert groups["美国节点"]["tolerance"] == 150
    assert groups["奈飞节点"]["filter"] == "(NF|奈飞|解锁|Netflix|NETFLIX|Media)"

    ui_only = {"流媒体", "国内服务", "更多策略"}
    rule_targets = {item["target"] for item in manifest["sources"]}
    rule_targets.update(item["target"] for item in manifest["inline_rules"])
    rule_targets.add(manifest["final_target"])
    assert ui_only.isdisjoint(rule_targets)
    assert all(groups[name].get("hidden", False) is False for name in ui_only)


def test_canonical_production_routes_only_through_acl4ssr(repo_root: Path) -> None:
    config = yaml.safe_load((repo_root / "config.yaml").read_text(encoding="utf-8"))
    assert config["rule_sources"]["acl4ssr"]["enabled"] is True
    assert config["modules"] == {"general": True}

    services = yaml.safe_load((repo_root / "services.yaml").read_text(encoding="utf-8"))
    assert services["services"] == []

    direct = yaml.safe_load((repo_root / "rules/direct.yaml").read_text(encoding="utf-8"))
    assert direct == {"version": 1, "rules": []}

    policies = yaml.safe_load((repo_root / "policies.yaml").read_text(encoding="utf-8"))
    assert set(policies["capabilities"]) == {"general"}
    assert policies["chains"] == []
    pools = {item["id"]: item for item in policies["pools"]}
    assert set(pools) == {
        "general",
        "ai_sg",
        "ai_jp",
        "ai_us",
        "ai_hk",
        "ai_tw",
        "ai_kr",
        "ai_other",
    }
    general = pools["general"]
    assert general["display_name"] == "__CR_GENERAL_INVENTORY"
    assert general["source_use"] == "general"
    assert general["excluded_capabilities"] == []
    assert {pools[item]["source_use"] for item in pools if item.startswith("ai_")} == {"ai"}

    for relative in (
        "rules/bulk.yaml",
        "rules/chatgpt.yaml",
        "rules/claude.yaml",
        "rules/emby.yaml",
        "rules/gemini.yaml",
        "rules/google_play.yaml",
    ):
        assert not (repo_root / relative).exists()


def test_acl4ssr_sources_become_inline_rule_providers_in_priority_order(
    project_factory, fixture_env, yaml_editor
) -> None:
    root, paths = project_factory()
    manifest_path = root / "rules/acl4ssr-test.yaml"
    manifest_path.write_text(
        dump_yaml(
            {
                "version": 1,
                "repository": "ACL4SSR/ACL4SSR",
                "ref": "0123456789abcdef0123456789abcdef01234567",
                "license": "CC-BY-SA-4.0",
                "max_source_bytes": 65536,
                "sources": [
                    {
                        "id": "ban_ad",
                        "path": "Clash/BanAD.list",
                        "target": "REJECT",
                        "priority": 20,
                    },
                    {
                        "id": "proxy_lite",
                        "path": "Clash/ProxyLite.list",
                        "target": "Proxy",
                        "priority": 800,
                        "module": "general",
                    },
                ],
                "inline_rules": [
                    {
                        "id": "geoip_cn",
                        "type": "GEOIP",
                        "value": "CN",
                        "target": "DIRECT",
                        "priority": 920,
                        "options": ["no-resolve"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def enable_acl4ssr(document):
        document["rule_sources"] = {
            "acl4ssr": {"enabled": True, "manifest": "rules/acl4ssr-test.yaml"}
        }

    yaml_editor(paths["config_path"], enable_acl4ssr)

    def fake_rule_fetcher(url: str, **_kwargs) -> str:
        if url.endswith("/Clash/BanAD.list"):
            return "DOMAIN-SUFFIX,ads.example\n"
        if url.endswith("/Clash/ProxyLite.list"):
            return "DOMAIN-SUFFIX,proxy.example\n"
        raise AssertionError(f"unexpected ACL4SSR URL: {url}")

    result = build_candidate(**paths, env=fixture_env, rule_fetcher=fake_rule_fetcher)
    rules = result.config["rules"]
    providers = result.config["rule-providers"]

    assert providers["acl4ssr_ban_ad"] == {
        "type": "inline",
        "behavior": "classical",
        "payload": ["DOMAIN-SUFFIX,ads.example"],
    }
    assert providers["acl4ssr_proxy_lite"] == {
        "type": "inline",
        "behavior": "classical",
        "payload": ["DOMAIN-SUFFIX,proxy.example"],
    }
    ad_rule = "RULE-SET,acl4ssr_ban_ad,REJECT"
    proxy_rule = "RULE-SET,acl4ssr_proxy_lite,Proxy"
    geoip_rule = "GEOIP,CN,DIRECT,no-resolve"
    assert rules.index(ad_rule) < rules.index(proxy_rule) < rules.index(geoip_rule)
    assert rules[-1] == "MATCH,Proxy"
    assert "ACL4SSR/ACL4SSR@0123456789abcdef0123456789abcdef01234567" in result.yaml_text
    assert "CC-BY-SA-4.0" in result.yaml_text
    assert result.report["rule_sources"]["acl4ssr"]["rule_providers"] == 2
    assert result.report["rule_sources"]["acl4ssr"]["rules"] == 3


def test_acl4ssr_policy_groups_are_lightweight_and_drive_final_target(
    project_factory, fixture_env, yaml_editor
) -> None:
    root, paths = project_factory()
    manifest_path = root / "rules/acl4ssr-policy-test.yaml"
    manifest_path.write_text(
        dump_yaml(
            {
                "version": 1,
                "repository": "ACL4SSR/ACL4SSR",
                "ref": "0123456789abcdef0123456789abcdef01234567",
                "license": "CC-BY-SA-4.0",
                "max_source_bytes": 65536,
                "groups": [
                    {
                        "id": "policy_auto",
                        "display_name": "Auto",
                        "module": "general",
                        "members": [{"auto_pool": "general"}],
                    },
                    {
                        "id": "policy_direct",
                        "display_name": "Direct",
                        "module": "general",
                        "members": [
                            {"builtin": "DIRECT"},
                            {"group": "Proxy"},
                            {"group": "Auto"},
                        ],
                    },
                    {
                        "id": "policy_microsoft",
                        "display_name": "Microsoft",
                        "module": "general",
                        "members": [{"group": "Direct"}, {"group": "Proxy"}],
                    },
                    {
                        "id": "policy_final",
                        "display_name": "Final",
                        "module": "general",
                        "members": [
                            {"group": "Proxy"},
                            {"group": "Direct"},
                            {"group": "Auto"},
                        ],
                    },
                ],
                "final_target": "Final",
                "sources": [
                    {
                        "id": "microsoft_rules",
                        "path": "Clash/Microsoft.list",
                        "target": "Microsoft",
                        "priority": 210,
                        "module": "general",
                    }
                ],
                "inline_rules": [],
            }
        ),
        encoding="utf-8",
    )

    def enable_acl4ssr(document):
        document["rule_sources"] = {
            "acl4ssr": {"enabled": True, "manifest": "rules/acl4ssr-policy-test.yaml"}
        }

    yaml_editor(paths["config_path"], enable_acl4ssr)
    result = build_candidate(
        **paths,
        env=fixture_env,
        rule_fetcher=lambda _url, **_kwargs: "DOMAIN-SUFFIX,microsoft.example\n",
    )
    groups = {item["name"]: item for item in result.config["proxy-groups"]}

    assert groups["Auto"]["proxies"] == ["__CR_AUTO_GENERAL_ANY"]
    assert "use" not in groups["Auto"]
    assert groups["Direct"]["proxies"] == ["DIRECT", "Proxy", "Auto"]
    assert groups["Microsoft"]["proxies"] == ["Direct", "Proxy"]
    assert groups["Final"]["proxies"] == ["Proxy", "Direct", "Auto"]
    assert "RULE-SET,acl4ssr_microsoft_rules,Microsoft" in result.config["rules"]
    assert result.config["rules"][-1] == "MATCH,Final"


def test_acl4ssr_manifest_rejects_routing_group_cycles(
    project_factory, fixture_env, yaml_editor
) -> None:
    root, paths = project_factory()
    manifest = {
        "version": 1,
        "repository": "ACL4SSR/ACL4SSR",
        "ref": "0123456789abcdef0123456789abcdef01234567",
        "license": "CC-BY-SA-4.0",
        "max_source_bytes": 65536,
        "groups": [
            {
                "id": "policy_a",
                "display_name": "Policy A",
                "module": "general",
                "members": [{"group": "Policy B"}],
            },
            {
                "id": "policy_b",
                "display_name": "Policy B",
                "module": "general",
                "members": [{"group": "Policy A"}],
            },
        ],
        "final_target": "Proxy",
        "sources": [
            {
                "id": "proxy_lite",
                "path": "Clash/ProxyLite.list",
                "target": "Proxy",
                "priority": 800,
                "module": "general",
            }
        ],
        "inline_rules": [],
    }
    (root / "rules/acl4ssr-test.yaml").write_text(dump_yaml(manifest), encoding="utf-8")

    def enable_acl4ssr(document):
        document["rule_sources"] = {
            "acl4ssr": {"enabled": True, "manifest": "rules/acl4ssr-test.yaml"}
        }

    yaml_editor(paths["config_path"], enable_acl4ssr)
    with pytest.raises(ConfigurationError, match="contain a cycle"):
        build_candidate(**paths, env=fixture_env)


def test_acl4ssr_manifest_rejects_repository_path_escape(
    project_factory, fixture_env, yaml_editor
) -> None:
    root, paths = project_factory()
    manifest = yaml.safe_load((root / "rules/acl4ssr.yaml").read_text(encoding="utf-8"))
    manifest["sources"][0]["path"] = "../private.list"
    (root / "rules/acl4ssr-test.yaml").write_text(dump_yaml(manifest), encoding="utf-8")

    def enable_acl4ssr(document):
        document["rule_sources"] = {
            "acl4ssr": {"enabled": True, "manifest": "rules/acl4ssr-test.yaml"}
        }

    yaml_editor(paths["config_path"], enable_acl4ssr)
    with pytest.raises(ConfigurationError, match="unsafe repository path"):
        build_candidate(**paths, env=fixture_env)
