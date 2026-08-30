from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from clash_relay.acl4ssr import parse_acl4ssr_list
from clash_relay.builder import build_candidate
from clash_relay.errors import ConfigurationError, GenerationError
from clash_relay.util import dump_yaml


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


def test_acl4ssr_manifest_is_immutable_and_attributed(repo_root: Path) -> None:
    manifest = yaml.safe_load((repo_root / "rules/acl4ssr.yaml").read_text(encoding="utf-8"))
    assert manifest["repository"] == "ACL4SSR/ACL4SSR"
    assert len(manifest["ref"]) == 40
    assert all(character in "0123456789abcdef" for character in manifest["ref"])
    assert manifest["ref"] != "master"
    assert manifest["license"] == "CC-BY-SA-4.0"
    assert manifest["final_target"] == "Final"
    assert {item["display_name"] for item in manifest["groups"]} == {
        "Auto",
        "Direct",
        "Block",
        "App Purify",
        "Google FCM",
        "Microsoft Bing",
        "Microsoft OneDrive",
        "Microsoft",
        "Apple",
        "Telegram",
        "AI",
        "NetEase Music",
        "Games",
        "YouTube",
        "Netflix",
        "Bahamut",
        "Bilibili",
        "Domestic Media",
        "Foreign Media",
        "Final",
    }
    source_ids = {item["id"] for item in manifest["sources"]}
    assert {
        "unban",
        "google_fcm",
        "ai",
        "openai",
        "youtube",
        "netflix",
        "proxy_gfwlist",
        "download",
    }.issubset(source_ids)


def test_canonical_production_routes_only_through_acl4ssr(repo_root: Path) -> None:
    config = yaml.safe_load((repo_root / "config.yaml").read_text(encoding="utf-8"))
    assert config["rule_sources"]["acl4ssr"]["enabled"] is True
    assert config["modules"]["general"] is True
    for module in ("chatgpt", "claude", "gemini", "google_play", "bulk"):
        assert config["modules"][module] is False

    policies = yaml.safe_load((repo_root / "policies.yaml").read_text(encoding="utf-8"))
    general = next(item for item in policies["pools"] if item["id"] == "general")
    assert "ai" not in general["excluded_capabilities"]


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
