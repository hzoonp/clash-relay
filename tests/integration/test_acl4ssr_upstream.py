from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from clash_relay.acl4ssr import load_acl4ssr_rules
from clash_relay.builder import build_candidate
from clash_relay.mihomo import validate_with_mihomo


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
def test_canonical_acl4ssr_pin_skips_no_legacy_rules(repo_root: Path) -> None:
    manifest = yaml.safe_load((repo_root / "rules/acl4ssr.yaml").read_text(encoding="utf-8"))
    _providers, _directives, report = load_acl4ssr_rules(
        manifest,
        modules={"general": True},
        timeout=20,
    )

    assert report is not None
    assert report["ref"] == "c498ae4911f15b19c5ceaef6f8737ca8705b4430"
    assert report["skipped_legacy_rules"] == 0
