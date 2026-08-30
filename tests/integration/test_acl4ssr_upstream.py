from __future__ import annotations

import os
from pathlib import Path

import pytest

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

    yaml_editor(paths["config_path"], enable_acl4ssr)
    result = build_candidate(**paths, env=fixture_env)
    acl_report = result.report["rule_sources"]["acl4ssr"]

    assert acl_report["repository"] == "ACL4SSR/ACL4SSR"
    assert len(acl_report["ref"]) == 40
    assert acl_report["rules"] > 500
    assert "GEOIP,CN,DIRECT,no-resolve" in result.config["rules"]
    assert result.config["rules"][-1] == "MATCH,Proxy"

    candidate = root / ".acl4ssr-candidate.yaml"
    candidate.write_text(result.yaml_text, encoding="utf-8")
    validation = validate_with_mihomo(
        Path(os.environ["MIHOMO_BIN"]),
        candidate,
        secret_values=result.secret_values,
    )
    assert validation["config_test"] == "passed"
    assert validation["startup_smoke"] == "passed"
