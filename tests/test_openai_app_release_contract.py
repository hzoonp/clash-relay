from __future__ import annotations

import tomllib
from pathlib import Path


def test_v163_openai_client_path_release_contract(repo_root: Path) -> None:
    with (repo_root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    changelog = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    docs = (repo_root / "docs" / "openai-app-reliability.md").read_text(encoding="utf-8")
    qualifier = (repo_root / "scripts" / "qualify_ai.py").read_text(encoding="utf-8")
    runtime = (repo_root / "src" / "clash_relay" / "ai_runtime_reliability.py").read_text(
        encoding="utf-8"
    )
    hardener = (repo_root / "scripts" / "harden_openai_runtime.py").read_text(encoding="utf-8")
    audit = (repo_root / "scripts" / "audit_production.py").read_text(encoding="utf-8")

    assert project["version"] == "1.6.3"
    assert "## [1.6.3] - 2026-09-03" in changelog
    assert "client-path" in readme
    assert "normal TLS certificate and hostname verification" in docs
    assert "does not restore managed Fake-IP DNS" in docs
    assert "exact bytes of a previously validated P24 release" in docs
    assert "openai_app_critical_probes" in qualifier
    assert "cache_service_key" in qualifier
    assert "openai_pass_ttl_seconds" in qualifier
    assert "android.chat.openai.com" in runtime
    assert "stable_first_fallback" in runtime
    assert 'result["runtime_status"]' in hardener
    assert 'result["status"] = "passed"' in hardener
    assert "audit_route_lock" in audit
    assert "allow-legacy-openai-client-path" in audit


def test_no_temporary_p24_or_p25_workflow_remains(repo_root: Path) -> None:
    workflows = repo_root / ".github" / "workflows"

    assert not list(workflows.glob("p24-*.yml"))
    assert not list(workflows.glob("p25-*.yml"))
