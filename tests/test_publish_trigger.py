from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"
CONFIG_EXAMPLE = ROOT / "config.example.yaml"


def test_publish_runs_on_main_and_manual_dispatch() -> None:
    text = WORKFLOW.read_text()
    assert "  push:\n" in text
    assert "      - main\n" in text
    assert "  workflow_dispatch:\n" in text
    assert "      publish:\n" in text
    assert "        default: false\n" in text
    assert "clash-relay-publish-${{ github.ref }}" in text
    assert "github.ref == 'refs/heads/main'" in text


def test_public_production_uses_one_ephemeral_job_and_no_sensitive_github_storage() -> None:
    text = WORKFLOW.read_text()
    assert "Build, qualify, validate, and optionally publish private config" in text
    assert "repository.private" not in text
    assert "actions/upload-artifact" not in text
    assert "actions/download-artifact" not in text
    assert "gh release" not in text
    assert "publish-gist" not in text
    assert "GITHUB_GIST_TOKEN" not in text
    assert "PUBLISH_PUBLIC_RELEASE" not in text
    assert text.count("continue-on-error: true") == 2
    assert "Remove private candidate" in text
    assert "if: always()" in text


def test_individual_subscription_urls_are_masked_before_generation() -> None:
    text = WORKFLOW.read_text()
    assert "Mask individual subscription URLs" in text
    assert "from clash_relay.secrets import load_secret_mapping" in text
    assert 'print(f"::add-mask::{command_escape(value)}")' in text
    assert text.index("Mask individual subscription URLs") < text.index(
        "Generate private candidate"
    )


def test_secrets_are_scoped_to_the_steps_that_need_them() -> None:
    text = WORKFLOW.read_text()
    assert text.count("CLASH_RELAY_SUBSCRIPTIONS: ${{ secrets.CLASH_RELAY_SUBSCRIPTIONS }}") == 2
    assert text.count("CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}") == 5
    load_history = text.index("Load private scheduler history")
    load_ai_cache = text.index("Load private AI qualification cache")
    qualify = text.index("Qualify private candidate through the unified pipeline")
    publish = text.index("Publish versioned validated release transaction")
    persist_cache = text.index("Persist private AI qualification cache")
    persist_history = text.index("Persist private scheduler history")
    assert load_history < load_ai_cache < qualify < publish < persist_cache < persist_history


def test_production_audit_runs_before_and_after_unified_qualification() -> None:
    text = WORKFLOW.read_text()
    assert "Audit source-to-scenario isolation" in text
    assert "Re-audit qualified candidate" in text
    assert text.count("python scripts/audit_production.py") == 2
    first_audit = text.index("Audit source-to-scenario isolation")
    qualifier = text.index("Qualify private candidate through the unified pipeline")
    second_audit = text.index("Re-audit qualified candidate")
    assert first_audit < qualifier < second_audit
    assert "--candidate .work/private/generated.yaml" in text
    assert "--candidate .work/private/config.yaml" in text
    assert "production-summary.md" in text


def test_qualification_is_one_staged_pipeline_with_legacy_executors_hidden_from_workflow() -> None:
    text = WORKFLOW.read_text()
    assert "python scripts/qualify_candidate.py" in text
    assert "--candidate .work/private/generated.yaml" in text
    assert "--output .work/private/config.yaml" in text
    assert "--stage-dir .work/private/stages" in text
    assert "--browsing-report .work/private/browsing-qualification-summary.json" in text
    assert "--ai-report .work/private/ai-qualification-summary.json" in text
    assert "python scripts/qualify_browsing.py" not in text
    assert "python scripts/qualify_ai.py" not in text
    assert ".work/bin/mihomo-qualification" in text
    assert "--tag v1." not in text


def test_scheduler_history_is_best_effort_derived_state_after_release_commit() -> None:
    text = WORKFLOW.read_text()
    load = text.index("Load private scheduler history")
    qualify = text.index("Qualify private candidate through the unified pipeline")
    publish = text.index("Publish versioned validated release transaction")
    persist = text.index("Persist private scheduler history")
    proof = text.index("Record publication result")
    assert "scripts/load_scheduler_history.py" in text
    assert "scripts/publish_scheduler_history.py" in text
    assert "--history .work/private/scheduler-history.json" in text
    assert "--history-key .work/private/scheduler-history.key" in text
    assert "--next-history .work/private/scheduler-history-next.json" in text
    assert load < qualify < publish < persist < proof
    persist_block = text[persist:proof]
    assert "continue-on-error: true" in persist_block
    assert "Derived state persistence" in text


def test_ai_cache_is_best_effort_incremental_state_after_release_commit() -> None:
    text = WORKFLOW.read_text()
    load = text.index("Load private AI qualification cache")
    qualify = text.index("Qualify private candidate through the unified pipeline")
    publish = text.index("Publish versioned validated release transaction")
    persist = text.index("Persist private AI qualification cache")
    history = text.index("Persist private scheduler history")
    assert "scripts/load_ai_qualification_cache.py" in text
    assert "scripts/publish_ai_qualification_cache.py" in text
    assert "--cache .work/private/ai-qualification-cache.json" in text
    assert "--cache-key .work/private/ai-qualification-cache.key" in text
    assert "--next-cache .work/private/ai-qualification-cache-next.json" in text
    assert load < qualify < publish < persist < history
    persist_block = text[persist:history]
    assert "continue-on-error: true" in persist_block
    assert "Live service probes" in text
    assert "Fresh cache pass/fail hits" in text


def test_versioned_release_transaction_replaces_snapshot_then_direct_publish() -> None:
    text = WORKFLOW.read_text()
    validation = text.index("Validate exact qualified candidate with the pinned stable Mihomo matrix")
    publish = text.index("Publish versioned validated release transaction")
    assert validation < publish
    assert "python scripts/publish_release_bundle.py" in text
    assert "scripts/snapshot_previous_config.py" not in text
    assert "clash-relay publish-cloudflare-kv" not in text


def test_manual_dispatch_is_dry_run_unless_publish_is_explicitly_enabled() -> None:
    text = WORKFLOW.read_text()
    assert "Record dry-run result" in text
    assert "github.event_name == 'workflow_dispatch' && inputs.publish != true" in text
    assert "github.event_name == 'push' || inputs.publish == true" in text
    assert "--publication-status dry-run" in text
    assert "--publication-status published" in text


def test_mihomo_validation_uses_manifest_matrix_without_workflow_version_constants() -> None:
    text = WORKFLOW.read_text()
    assert "python scripts/validate_mihomo_matrix.py" in text
    assert "--manifest tools/mihomo-versions.json" in text
    assert "Detailed core output is intentionally suppressed" in text
    assert "v1.19.30" not in text
    assert "v1.19.29" not in text
    assert text.index("validate_mihomo_matrix.py") < text.index(
        "Publish versioned validated release transaction"
    )


def test_final_production_proof_uses_matrix_report_and_private_aggregate_inputs() -> None:
    text = WORKFLOW.read_text()
    assert "scripts/render_production_proof.py" in text
    assert "--audit .work/private/post-qualification-audit.json" in text
    assert "--browsing .work/private/browsing-qualification-summary.json" in text
    assert "--ai .work/private/ai-qualification-summary.json" in text
    assert "--validated-cores-report .work/private/mihomo-validation-matrix.json" in text
    assert "production-proof.md" in text
    assert 'cat .work/private/production-proof.md >> "$GITHUB_STEP_SUMMARY"' in text
    assert text.index("validate_mihomo_matrix.py") < text.index("--publication-status dry-run")
    assert text.index("Publish versioned validated release transaction") < text.rindex(
        "--publication-status published"
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
