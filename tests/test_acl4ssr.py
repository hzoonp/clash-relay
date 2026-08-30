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


def test_acl4ssr_rules_are_prioritized_and_inlined(
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

    ad_rule = "DOMAIN-SUFFIX,ads.example,REJECT"
    proxy_rule = "DOMAIN-SUFFIX,proxy.example,Proxy"
    geoip_rule = "GEOIP,CN,DIRECT,no-resolve"
    assert rules.index(ad_rule) < rules.index(proxy_rule) < rules.index(geoip_rule)
    assert rules[-1] == "MATCH,Proxy"
    assert "ACL4SSR/ACL4SSR@0123456789abcdef0123456789abcdef01234567" in result.yaml_text
    assert "CC-BY-SA-4.0" in result.yaml_text
    assert result.report["rule_sources"]["acl4ssr"]["rules"] == 3


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
