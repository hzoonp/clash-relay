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

    publish = lifecycle.index("release = self._publish_release()")
    ai = lifecycle.index('stage="persist_ai_qualification_cache"')
    history = lifecycle.index('stage="persist_scheduler_history"')
    proof = lifecycle.index("proof = self._render_existing_proof(release=release)")
    assert publish < ai < history < proof
    assert lifecycle.count("best_effort=True") == 2
    assert "finally:" in lifecycle
    assert "shutil.rmtree(self.paths.private_dir" in lifecycle
