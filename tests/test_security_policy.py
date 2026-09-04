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

    assert publish["permissions"] == {"contents": "read"}
    assert rollback["permissions"] == {"contents": "read"}
    assert release["permissions"] == {"contents": "write"}


def test_sensitive_github_storage_remains_absent_from_production(repo_root: Path) -> None:
    workflow = (repo_root / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    lifecycle = (repo_root / "src" / "clash_relay" / "production_lifecycle.py").read_text(
        encoding="utf-8"
    )

    assert "actions/upload-artifact" not in workflow
    assert "gh release" not in workflow
    assert "publish-gist" not in workflow
    assert '      - "scripts/**"' in workflow
    assert "continue-on-error" not in workflow
    assert workflow.count("python scripts/run_production_release.py") == 1

    run_body = lifecycle[lifecycle.index("    def run(self)") :]
    publish = run_body.index("release = self._publish_release(project)")
    derived_state = run_body.index("derived_state = self._persist_derived_state(project)")
    proof = run_body.index("proof = self._post_commit_proof(release=release)")
    manifest = run_body.index("manifest = self._post_commit_manifest(")
    metrics = run_body.index("metrics = self._persist_production_metrics(project)")
    assert publish < derived_state < proof < manifest < metrics

    persist_start = lifecycle.index("    def _persist_derived_state(")
    persist_end = lifecycle.index("    def _persist_production_metrics", persist_start)
    persist_body = lifecycle[persist_start:persist_end]
    ai = persist_body.index('"persist_ai_qualification_cache",')
    history = persist_body.index('"persist_scheduler_history",')
    assert ai < history
    assert persist_body.count("self._best_effort_state(") == 2

    metrics_start = lifecycle.index("    def _persist_production_metrics(")
    metrics_end = lifecycle.index("    def _render_existing_proof", metrics_start)
    metrics_body = lifecycle[metrics_start:metrics_end]
    assert '"persist_production_metrics",' in metrics_body
    assert metrics_body.count("self._best_effort_state(") == 1

    assert 'self.warnings.append("render_production_proof")' in lifecycle
    assert 'self.warnings.append("render_release_manifest")' in lifecycle
    assert "finally:" in run_body
    assert "shutil.rmtree(self.paths.private_dir" in run_body
