from __future__ import annotations

from pathlib import Path


def test_openai_client_path_release_contract(repo_root: Path) -> None:
    release_notes = (repo_root / "docs" / "releases" / "2.0.0.md").read_text(encoding="utf-8")
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    docs = (repo_root / "docs" / "openai-app-reliability.md").read_text(encoding="utf-8")
    qualifier = (repo_root / "scripts" / "qualify_ai.py").read_text(encoding="utf-8")
    ai_application = (repo_root / "src" / "clash_relay" / "ai_application.py").read_text(
        encoding="utf-8"
    )
    service_qualification = (
        repo_root / "src" / "clash_relay" / "service_qualification.py"
    ).read_text(encoding="utf-8")
    scheduling = (repo_root / "policies" / "scheduling.yaml").read_text(encoding="utf-8")
    runtime = (repo_root / "src" / "clash_relay" / "ai_runtime_reliability.py").read_text(
        encoding="utf-8"
    )
    hardener = (repo_root / "scripts" / "harden_openai_runtime.py").read_text(encoding="utf-8")
    openai_application = (repo_root / "src" / "clash_relay" / "openai_application.py").read_text(
        encoding="utf-8"
    )
    audit_adapter = (repo_root / "scripts" / "audit_production.py").read_text(encoding="utf-8")
    pipeline = (repo_root / "src" / "clash_relay" / "production_pipeline.py").read_text(
        encoding="utf-8"
    )

    assert "# clash-relay 2.0.0" in release_notes
    assert "ServiceQualification" in release_notes
    assert "client-path" in readme
    assert "normal TLS certificate and hostname verification" in docs
    assert "does not restore managed Fake-IP DNS" in docs
    assert "historical exact bytes" in docs
    assert "current" in docs and "client-path" in docs
    assert "run_ai_qualification" in qualifier
    assert "openai_app_contract" not in ai_application
    assert 'if name == "ai_openai"' not in ai_application
    assert "OpenAIQualification" in service_qualification
    assert "critical_probes" in service_qualification
    assert "cache_service_key" in service_qualification
    assert "openai_pass_ttl_seconds" in service_qualification
    assert "client_path_hardening: true" in scheduling
    assert "android.chat.openai.com" in runtime
    assert "stable_first_fallback" in runtime
    assert "harden_openai_client_path" in hardener
    assert "rewrite_openai_client_path_candidate" not in hardener
    assert 'report["runtime_status"]' in openai_application
    assert 'report["status"] = "passed"' in openai_application
    assert "audit_route_lock" in pipeline
    assert "audit_openai_client_path" in pipeline
    legacy_flag = "allow-legacy-" + "openai-client-path"
    assert legacy_flag not in audit_adapter
    assert "audit_candidate" in audit_adapter


def test_no_temporary_phase_workflow_remains(repo_root: Path) -> None:
    workflows = repo_root / ".github" / "workflows"

    assert not list(workflows.glob("p[0-9]*.yml"))
    assert not list(workflows.glob("p[0-9]*.yaml"))
