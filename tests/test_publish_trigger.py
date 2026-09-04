from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from clash_relay.errors import ValidationError
from clash_relay.production_lifecycle import resolve_publication_mode

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"
CONFIG_EXAMPLE = ROOT / "config.example.yaml"
MASK_SCRIPT = ROOT / "scripts" / "mask_subscription_secrets.py"
LIFECYCLE = ROOT / "src" / "clash_relay" / "production_lifecycle.py"


def test_publish_runs_on_main_schedule_and_manual_dispatch() -> None:
    text = WORKFLOW.read_text()
    assert "  push:\n" in text
    assert "      - main\n" in text
    assert "  schedule:\n" in text
    assert '    - cron: "17 */6 * * *"\n' in text
    assert "  workflow_dispatch:\n" in text
    assert "      publish:\n" in text
    assert "        default: false\n" in text
    assert "clash-relay-publish-${{ github.ref }}" in text
    assert "github.ref == 'refs/heads/main'" in text


def test_workflow_is_a_thin_adapter_to_one_production_entrypoint() -> None:
    text = WORKFLOW.read_text()
    assert len(text.splitlines()) < 100
    assert text.count("python scripts/run_production_release.py") == 1
    assert "Resolve publication mode" not in text
    assert "push|schedule)" not in text
    assert "python - <<" not in text
    for leaf in (
        "run_production_pipeline.py",
        "fetch_current_config.py",
        "check_promotion_guard.py",
        "validate_mihomo_matrix.py",
        "publish_release_bundle.py",
        "render_production_proof.py",
    ):
        assert leaf not in text


def test_public_production_uses_one_ephemeral_job_and_no_sensitive_github_storage() -> None:
    text = WORKFLOW.read_text()
    assert "Run canonical production lifecycle" in text
    assert "repository.private" not in text
    assert "actions/upload-artifact" not in text
    assert "actions/download-artifact" not in text
    assert "gh release" not in text
    assert "publish-gist" not in text
    assert "GITHUB_GIST_TOKEN" not in text


def test_individual_subscription_urls_are_masked_before_pipeline() -> None:
    workflow = WORKFLOW.read_text()
    masker = MASK_SCRIPT.read_text()
    assert "Mask individual subscription URLs" in workflow
    assert "python scripts/mask_subscription_secrets.py" in workflow
    assert "from clash_relay.secrets import load_secret_mapping" in masker
    assert "::add-mask::" in masker
    assert workflow.index("Mask individual subscription URLs") < workflow.index(
        "Run production pipeline"
    )


def test_secrets_are_scoped_to_the_single_pipeline_step() -> None:
    text = WORKFLOW.read_text()
    assert text.count("CLASH_RELAY_SUBSCRIPTIONS: ${{ secrets.CLASH_RELAY_SUBSCRIPTIONS }}") == 2
    assert text.count("CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}") == 1
    assert text.count("CLOUDFLARE_ACCOUNT_ID: ${{ vars.CLOUDFLARE_ACCOUNT_ID }}") == 1
    assert (
        text.count("CLOUDFLARE_KV_NAMESPACE_TITLE: ${{ vars.CLOUDFLARE_KV_NAMESPACE_TITLE }}") == 1
    )


def test_application_pipeline_owns_full_production_order_and_cleanup() -> None:
    text = LIFECYCLE.read_text()
    stages = [
        "generation = self._generate()",
        "self._load_derived_state(project)",
        "binary = self._download_primary_mihomo()",
        "pipeline = self._qualify(binary)",
        "promotion = self._promotion_guard(project)",
        "matrix = self._validate_matrix(binary)",
        "release = self._publish_release(project)",
        "derived_state = self._persist_derived_state(project)",
        "proof = self._post_commit_proof(release=release)",
        "manifest = self._post_commit_manifest(",
        "metrics = self._persist_production_metrics(project)",
    ]
    positions = [text.index(stage) for stage in stages]
    assert positions == sorted(positions)
    assert "run_production_pipeline(" in text
    assert "run_promotion_guard(" in text
    assert "validate_mihomo_matrix(" in text
    assert "publish_production_release(" in text
    assert "check_promotion_guard.py" not in text
    assert "validate_mihomo_matrix.py" not in text
    assert "publish_release_bundle.py" not in text
    assert "finally:" in text
    assert "shutil.rmtree(self.paths.private_dir" in text


def test_derived_state_persistence_remains_post_commit_and_best_effort() -> None:
    text = LIFECYCLE.read_text()
    publish = text.index("release = self._publish_release(project)")
    persist = text.index("derived_state = self._persist_derived_state(project)")
    assert publish < persist
    assert text.count("self._best_effort_state(") == 3
    assert "persist_ai_qualification_cache" in text
    assert "persist_scheduler_history" in text


def test_manual_dispatch_is_dry_run_unless_publish_is_explicitly_enabled() -> None:
    assert resolve_publication_mode(event_name="push") is True
    assert resolve_publication_mode(event_name="schedule") is True
    assert resolve_publication_mode(event_name="workflow_dispatch", manual_publish="true") is True
    assert resolve_publication_mode(event_name="workflow_dispatch", manual_publish="false") is False
    assert resolve_publication_mode(explicit_publish=False, event_name="push") is False
    assert resolve_publication_mode(explicit_publish=True) is True
    with pytest.raises(ValidationError, match="unsupported"):
        resolve_publication_mode(event_name="pull_request")


def test_mihomo_validation_uses_manifest_matrix_without_workflow_version_constants() -> None:
    workflow = WORKFLOW.read_text()
    lifecycle = LIFECYCLE.read_text()
    assert "tools/mihomo-versions.json" in lifecycle
    assert "validate_mihomo_matrix(" in lifecycle
    assert "validate_mihomo_matrix.py" not in lifecycle
    assert "v1.19.30" not in workflow
    assert "v1.19.29" not in workflow
    assert "v1.19.30" not in lifecycle
    assert "v1.19.29" not in lifecycle


def test_release_manifest_is_rendered_after_existing_production_proof() -> None:
    text = LIFECYCLE.read_text()
    assert "release-manifest.json" in text
    assert "release-manifest.md" in text
    assert text.index("proof = self._post_commit_proof") < text.index(
        "manifest = self._post_commit_manifest"
    )


def test_sensitive_github_backends_are_disabled_by_default() -> None:
    config = yaml.safe_load(CONFIG_EXAMPLE.read_text())
    publishing = config["publishing"]
    assert publishing["artifact"] is False
    assert publishing["github_release"]["enabled"] is False
    assert publishing["github_release"]["allow_sensitive_public_release"] is False
    assert publishing["gist"]["enabled"] is False
    assert publishing["gist"]["allow_sensitive_unlisted_gist"] is False
    assert publishing["cloudflare_kv"] == {"enabled": True, "key": "production-config"}
