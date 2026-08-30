from __future__ import annotations

import os
from pathlib import Path

import pytest

from clash_relay.builder import build_candidate
from clash_relay.mihomo import validate_with_mihomo
from clash_relay.util import dump_yaml


@pytest.mark.integration
def test_source_filtered_routes_validate_with_real_mihomo(
    project_factory,
    fixture_env,
    yaml_editor,
) -> None:
    root, paths = project_factory()

    def configure_modules(document):
        for module in document["modules"]:
            document["modules"][module] = module == "general"
        document["rule_sources"] = {
            "acl4ssr": {"enabled": True, "manifest": "rules/source-filter-test.yaml"}
        }

    def configure_subscriptions(document):
        for item in document["subscriptions"]:
            item["enabled"] = item["id"] in {"primary", "secondary"}

    yaml_editor(paths["config_path"], configure_modules)
    yaml_editor(paths["subscriptions_path"], configure_subscriptions)

    manifest = {
        "version": 1,
        "repository": "ACL4SSR/ACL4SSR",
        "ref": "0123456789abcdef0123456789abcdef01234567",
        "license": "CC-BY-SA-4.0",
        "max_source_bytes": 65536,
        "groups": [
            {
                "id": "policy_filtered",
                "display_name": "Filtered",
                "module": "general",
                "excluded_sources": ["primary"],
                "members": [{"group": "Proxy"}, {"builtin": "DIRECT"}],
            }
        ],
        "final_target": "Proxy",
        "final_excluded_sources": ["primary"],
        "sources": [
            {
                "id": "group_rules",
                "path": "Clash/Group.list",
                "target": "Filtered",
                "priority": 100,
                "module": "general",
            },
            {
                "id": "strict_rules",
                "path": "Clash/Strict.list",
                "target": "Proxy",
                "priority": 110,
                "module": "general",
                "excluded_sources": ["primary"],
            },
        ],
        "inline_rules": [],
    }
    (root / "rules/source-filter-test.yaml").write_text(dump_yaml(manifest), encoding="utf-8")

    def fake_rule_fetcher(url: str, **_kwargs) -> str:
        if url.endswith("/Clash/Group.list"):
            return "DOMAIN-SUFFIX,group.example\n"
        if url.endswith("/Clash/Strict.list"):
            return "DOMAIN-SUFFIX,strict.example\n"
        raise AssertionError(f"unexpected ACL4SSR URL: {url}")

    result = build_candidate(
        **paths,
        env=fixture_env,
        rule_fetcher=fake_rule_fetcher,
    )
    groups = {item["name"]: item for item in result.config["proxy-groups"]}
    filtered = groups["Filtered"]["proxies"][0]

    assert filtered.startswith("__CR_AUTO_FILTER_")
    assert groups[filtered]["use"] == ["cr_general_any"]
    assert "exclude-filter" in groups[filtered]
    assert "primary" in groups[filtered]["exclude-filter"]
    assert set(result.config["proxy-providers"]) == {"cr_general_any"}
    assert f"RULE-SET,acl4ssr_strict_rules,{filtered}" in result.config["rules"]
    assert result.config["rules"][-1] == f"MATCH,{filtered}"

    candidate = root / ".source-filter-candidate.yaml"
    candidate.write_text(result.yaml_text, encoding="utf-8")
    validation = validate_with_mihomo(
        Path(os.environ["MIHOMO_BIN"]),
        candidate,
        secret_values=result.secret_values,
    )
    assert validation["config_test"] == "passed"
    assert validation["startup_smoke"] == "passed"
