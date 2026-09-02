from __future__ import annotations

import tomllib
from pathlib import Path


def test_v161_openai_app_hotfix_release_contract(repo_root: Path) -> None:
    with (repo_root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    changelog = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    docs = (repo_root / "docs" / "openai-app-reliability.md").read_text(encoding="utf-8")
    qualifier = (repo_root / "scripts" / "qualify_ai.py").read_text(encoding="utf-8")
    audit = (repo_root / "scripts" / "audit_production.py").read_text(encoding="utf-8")

    assert project["version"] == "1.6.1"
    assert "## [1.6.1] - 2026-09-02" in changelog
    assert "OpenAI App reliability" in readme
    assert "normal TLS certificate and hostname verification" in docs
    assert "does not restore managed Fake-IP DNS" in docs
    assert "openai_app_critical_probes" in qualifier
    assert "cache_service_key" in qualifier
    assert "rewrite_route_locked_candidate" in qualifier
    assert "audit_route_lock" in audit


def test_no_temporary_p24_workflow_remains(repo_root: Path) -> None:
    workflows = repo_root / ".github" / "workflows"

    assert not list(workflows.glob("p24-*.yml"))
