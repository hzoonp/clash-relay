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
    assert "continue-on-error" not in text
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
    assert text.count("CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}") == 1
    assert text.index("CLOUDFLARE_API_TOKEN") > text.index("validate_core v1.19.29")


def test_production_audit_runs_before_and_after_private_qualification() -> None:
    text = WORKFLOW.read_text()
    assert "Audit source-to-scenario isolation" in text
    assert "Re-audit qualified candidate" in text
    assert text.count("python scripts/audit_production.py") == 2
    first_audit = text.index("Audit source-to-scenario isolation")
    browsing_qualifier = text.index("Qualify browsing nodes")
    ai_qualifier = text.index("Qualify AI nodes by country")
    second_audit = text.index("Re-audit qualified candidate")
    assert first_audit < browsing_qualifier < ai_qualifier < second_audit
    assert "production-summary.md" in text
    assert 'cat .work/private/production-summary.md >> "$GITHUB_STEP_SUMMARY"' in text


def test_browsing_qualification_precedes_ai_and_final_validation() -> None:
    text = WORKFLOW.read_text()
    assert "Download Mihomo for private qualification" in text
    assert "python scripts/qualify_browsing.py" in text
    assert "--attempts 3" in text
    assert "--required-successes 2" in text
    assert "Append safe browsing qualification summary" in text
    assert "node-level results remain private" in text
    browsing = text.index("python scripts/qualify_browsing.py")
    ai = text.index("python scripts/qualify_ai.py")
    assert browsing < ai < text.index("validate_core v1.19.30")
    assert ".work/bin/mihomo-qualification" in text
    assert text.count("--mihomo-bin .work/bin/mihomo-qualification") == 2


def test_ai_qualification_precedes_final_validation() -> None:
    text = WORKFLOW.read_text()
    assert "Qualify AI nodes by country" in text
    assert "python scripts/qualify_ai.py" in text
    qualifier = text.index("python scripts/qualify_ai.py")
    assert qualifier < text.index("validate_core v1.19.30")
    assert qualifier < text.index("validate_core v1.19.29")
    assert "Append safe AI qualification summary" in text


def test_manual_dispatch_is_dry_run_unless_publish_is_explicitly_enabled() -> None:
    text = WORKFLOW.read_text()
    assert "Record dry-run result" in text
    assert "github.event_name == 'workflow_dispatch' && inputs.publish != true" in text
    assert "github.event_name == 'push' || inputs.publish == true" in text
    assert "was **not** written to Cloudflare KV" in text


def test_both_mihomo_versions_pass_before_cloudflare_publication() -> None:
    text = WORKFLOW.read_text()
    assert "validate_core v1.19.30" in text
    assert "validate_core v1.19.29" in text
    assert "Detailed core output is intentionally suppressed" in text
    publish = text.index("Publish exact validated bytes to Cloudflare KV")
    assert text.index("validate_core v1.19.30") < publish
    assert text.index("validate_core v1.19.29") < publish
    assert "clash-relay publish-cloudflare-kv" in text


def test_sensitive_github_backends_are_disabled_by_default() -> None:
    config = yaml.safe_load(CONFIG_EXAMPLE.read_text())
    publishing = config["publishing"]
    assert publishing["artifact"] is False
    assert publishing["github_release"]["enabled"] is False
    assert publishing["github_release"]["allow_sensitive_public_release"] is False
    assert publishing["gist"]["enabled"] is False
    assert publishing["gist"]["allow_sensitive_unlisted_gist"] is False
    assert publishing["cloudflare_kv"] == {"enabled": True, "key": "production-config"}
