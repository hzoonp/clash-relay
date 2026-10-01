from __future__ import annotations

from pathlib import Path

import yaml


def test_canonical_generation_disables_unsafe_subscription_modes(repo_root: Path) -> None:
    config = yaml.safe_load((repo_root / "config.yaml").read_text(encoding="utf-8"))
    generation = config["generation"]

    assert generation["allow_http_subscription_urls"] is False
    assert generation["allow_file_subscription_urls"] is False
    assert generation["reject_private_proxy_hosts"] is True
    assert generation["max_subscription_bytes"] <= 8 * 1024 * 1024


def test_security_policy_documents_private_operational_state_and_token_scope(
    repo_root: Path,
) -> None:
    text = (repo_root / "SECURITY.md").read_text(encoding="utf-8")

    assert "dedicated Cloudflare API token" in text
    assert "global API key" in text
    assert "DNS-resolved before use" in text
    assert "response bytes are bounded" in text
    assert "scheduler history" in text
    assert "AI qualification cache" in text
    assert "previous-good" in text
    assert "must never widen source permissions" in text


def test_workflow_permissions_keep_production_read_only(repo_root: Path) -> None:
    publish = yaml.load(
        (repo_root / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    rollback = yaml.load(
        (repo_root / ".github" / "workflows" / "rollback.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    release = yaml.load(
        (repo_root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )

    assert publish["permissions"] == {"actions": "read", "contents": "read"}
    assert rollback["permissions"] == {"contents": "read"}
    assert release["permissions"] == {"actions": "read", "contents": "write"}


def test_sensitive_github_storage_remains_absent_from_production(repo_root: Path) -> None:
    workflow = (repo_root / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    lifecycle = (repo_root / "src" / "clash_relay" / "production_lifecycle.py").read_text(
        encoding="utf-8"
    )
    observability = (repo_root / "src" / "clash_relay" / "production_observability.py").read_text(
        encoding="utf-8"
    )

    assert "actions/upload-artifact" not in workflow
    assert "gh release" not in workflow
    assert "publish-gist" not in workflow
    assert "github.event_name != 'push'" in workflow
    assert "continue-on-error" not in workflow
    assert workflow.count("python scripts/run_production_release.py") == 1

    run_body = lifecycle[lifecycle.index("    def run(self)") :]
    release_boundary = run_body.index(
        "release_stage = self._release_candidate_stage(project, binary)"
    )
    derived_state = run_body.index("derived_state = self._persist_derived_state(project)")
    proof = run_body.index("proof = self._post_commit_proof(release=release)")
    manifest = run_body.index("manifest = self._post_commit_manifest(")
    optional = run_body.index("observability = publish_post_release_observability(")
    assert release_boundary < derived_state < proof < manifest < optional

    persist_start = lifecycle.index("    def _persist_derived_state(")
    persist_end = lifecycle.index("    def _safe_source_admission_summary", persist_start)
    persist_body = lifecycle[persist_start:persist_end]
    ai = persist_body.index('"persist_ai_qualification_cache",')
    history = persist_body.index('"persist_scheduler_history",')
    assert ai < history
    assert persist_body.count("self._best_effort_state(") == 2

    assert "_persist_production_metrics" not in lifecycle
    assert "_publish_scheduler_observation" not in lifecycle
    assert "_record_operational_slo" not in lifecycle
    assert "persist_production_metrics(" in observability
    assert "_publish_scheduler_observation(" in observability
    assert "_record_operational_slo(" in observability

    assert 'self.warnings.append("render_production_proof")' in lifecycle
    assert 'self.warnings.append("render_release_manifest")' in lifecycle
    assert "finally:" in run_body
    assert "shutil.rmtree(self.paths.private_dir" in run_body
