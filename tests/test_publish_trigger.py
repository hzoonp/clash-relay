from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"
CONFIG_EXAMPLE = ROOT / "config.example.yaml"
MASK_SCRIPT = ROOT / "scripts" / "mask_subscription_secrets.py"
PIPELINE = ROOT / "src" / "clash_relay" / "production_pipeline.py"


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


def test_publication_mode_unifies_push_schedule_and_manual_dispatch() -> None:
    text = WORKFLOW.read_text()
    assert "publish_requested: ${{ steps.mode.outputs.publish_requested }}" in text
    assert "Resolve publication mode" in text
    assert "push|schedule)" in text
    assert "workflow_dispatch)" in text
    assert 'if [[ "$MANUAL_PUBLISH" == "true" ]]' in text
    assert 'echo "publish_requested=$publish_requested" >> "$GITHUB_OUTPUT"' in text
    assert "Unsupported publication event" in text


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
    workflow = WORKFLOW.read_text()
    masker = MASK_SCRIPT.read_text()
    assert "Mask individual subscription URLs" in workflow
    assert "python scripts/mask_subscription_secrets.py" in workflow
    assert "from clash_relay.secrets import load_secret_mapping" in masker
    assert "::add-mask::" in masker
    assert workflow.index("Mask individual subscription URLs") < workflow.index(
        "Generate private candidate"
    )


def test_secrets_are_scoped_to_the_steps_that_need_them() -> None:
    text = WORKFLOW.read_text()
    assert text.count("CLASH_RELAY_SUBSCRIPTIONS: ${{ secrets.CLASH_RELAY_SUBSCRIPTIONS }}") == 2
    assert text.count("CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}") == 6
    load_history = text.index("Load private scheduler history")
    load_ai_cache = text.index("Load private AI qualification cache")
    pipeline = text.index("Run unified private production pipeline")
    baseline = text.index("Fetch current production baseline")
    guard = text.index("Enforce production promotion guard")
    publish = text.index("Publish versioned validated release transaction")
    persist_cache = text.index("Persist private AI qualification cache")
    persist_history = text.index("Persist private scheduler history")
    assert load_history < load_ai_cache < pipeline < baseline < guard < publish
    assert publish < persist_cache < persist_history


def test_production_audit_runs_inside_one_application_pipeline_before_and_after_qualification() -> (
    None
):
    workflow = WORKFLOW.read_text()
    pipeline = PIPELINE.read_text()
    assert workflow.count("python scripts/run_production_pipeline.py") == 1
    assert "python scripts/audit_production.py" not in workflow
    assert "--pre-audit .work/private/production-audit.json" in workflow
    assert "--post-audit .work/private/post-qualification-audit.json" in workflow
    assert "pre_audit = audit_candidate" in pipeline
    assert "qualification = run_qualification_pipeline" in pipeline
    assert "post_audit = audit_candidate" in pipeline
    assert pipeline.index("pre_audit = audit_candidate") < pipeline.index(
        "qualification = run_qualification_pipeline"
    )
    assert pipeline.index("qualification = run_qualification_pipeline") < pipeline.index(
        "post_audit = audit_candidate"
    )


def test_qualification_is_one_staged_application_pipeline_with_legacy_executors_hidden() -> None:
    text = WORKFLOW.read_text()
    assert "python scripts/run_production_pipeline.py" in text
    assert "--candidate .work/private/generated.yaml" in text
    assert "--output .work/private/config.yaml" in text
    assert "--stage-dir .work/private/stages" in text
    assert "--browsing-report .work/private/browsing-qualification-summary.json" in text
    assert "--ai-report .work/private/ai-qualification-summary.json" in text
    assert "python scripts/qualify_candidate.py" not in text
    assert "python scripts/qualify_browsing.py" not in text
    assert "python scripts/qualify_ai.py" not in text
    assert ".work/bin/mihomo-qualification" in text
    assert "--tag v1." not in text


def test_scheduler_history_is_best_effort_derived_state_after_release_commit() -> None:
    text = WORKFLOW.read_text()
    load = text.index("Load private scheduler history")
    pipeline = text.index("Run unified private production pipeline")
    publish = text.index("Publish versioned validated release transaction")
    persist = text.index("Persist private scheduler history")
    proof = text.index("Record publication result")
    assert "scripts/load_scheduler_history.py" in text
    assert "scripts/publish_scheduler_history.py" in text
    assert "--history .work/private/scheduler-history.json" in text
    assert "--history-key .work/private/scheduler-history.key" in text
    assert "--next-history .work/private/scheduler-history-next.json" in text
    assert load < pipeline < publish < persist < proof
    persist_block = text[persist:proof]
    assert "continue-on-error: true" in persist_block
    assert "Derived state persistence" in text


def test_ai_cache_is_best_effort_incremental_state_after_release_commit() -> None:
    workflow = WORKFLOW.read_text()
    pipeline_source = PIPELINE.read_text()
    load = workflow.index("Load private AI qualification cache")
    pipeline = workflow.index("Run unified private production pipeline")
    publish = workflow.index("Publish versioned validated release transaction")
    persist = workflow.index("Persist private AI qualification cache")
    history = workflow.index("Persist private scheduler history")
    assert "scripts/load_ai_qualification_cache.py" in workflow
    assert "scripts/publish_ai_qualification_cache.py" in workflow
    assert "--cache .work/private/ai-qualification-cache.json" in workflow
    assert "--cache-key .work/private/ai-qualification-cache.key" in workflow
    assert "--next-cache .work/private/ai-qualification-cache-next.json" in workflow
    assert load < pipeline < publish < persist < history
    persist_block = workflow[persist:history]
    assert "continue-on-error: true" in persist_block
    assert "Live service probes" in pipeline_source
    assert "Fresh cache pass/fail hits" in pipeline_source


def test_promotion_guard_precedes_matrix_and_release_transaction() -> None:
    text = WORKFLOW.read_text()
    baseline = text.index("Fetch current production baseline")
    guard = text.index("Enforce production promotion guard")
    validation = text.index(
        "Validate exact qualified candidate with the pinned stable Mihomo matrix"
    )
    publish = text.index("Publish versioned validated release transaction")
    assert baseline < guard < validation < publish
    assert "python scripts/fetch_current_config.py" in text[baseline:guard]
    assert "python scripts/check_promotion_guard.py" in text[guard:validation]
    assert "python scripts/publish_release_bundle.py" in text[publish:]
    assert "scripts/snapshot_previous_config.py" not in text
    assert "clash-relay publish-cloudflare-kv" not in text


def test_manual_dispatch_is_dry_run_unless_publish_is_explicitly_enabled() -> None:
    text = WORKFLOW.read_text()
    assert "Record dry-run result" in text
    assert "needs.prepare.outputs.publish_requested != 'true'" in text
    assert "needs.prepare.outputs.publish_requested == 'true'" in text
    assert "github.event_name == 'workflow_dispatch' && inputs.publish != true" not in text
    assert "github.event_name == 'push' || inputs.publish == true" not in text
    assert "--publication-status dry-run" in text
    assert "--publication-status published" in text


def test_scheduled_refresh_uses_the_full_fail_closed_production_path() -> None:
    text = WORKFLOW.read_text()
    generate = text.index("Generate private candidate")
    pipeline = text.index("Run unified private production pipeline")
    baseline = text.index("Fetch current production baseline")
    guard = text.index("Enforce production promotion guard")
    matrix = text.index("Validate exact qualified candidate with the pinned stable Mihomo matrix")
    publish = text.index("Publish versioned validated release transaction")
    assert generate < pipeline < baseline < guard < matrix < publish
    assert "if: github.event_name == 'schedule'" not in text
    assert "github.event_name" not in text[generate:publish]


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
