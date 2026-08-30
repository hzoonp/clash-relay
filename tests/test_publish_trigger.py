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
    assert "clash-relay-publish-${{ github.ref }}" in text
    assert "github.ref == 'refs/heads/main'" in text


def test_public_production_uses_one_ephemeral_job_and_no_sensitive_github_storage() -> None:
    text = WORKFLOW.read_text()
    assert "Build, qualify, validate, and publish private config" in text
    assert "repository.private" not in text
    assert "actions/upload-artifact" not in text
    assert "actions/download-artifact" not in text
    assert "gh release" not in text
    assert "publish-gist" not in text
    assert "GITHUB_GIST_TOKEN" not in text
    assert "PUBLISH_PUBLIC_RELEASE" not in text
    assert "always()" not in text
    assert "continue-on-error" not in text


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


def test_ai_qualification_precedes_final_validation() -> None:
    text = WORKFLOW.read_text()
    assert "Qualify AI nodes by country" in text
    assert "python scripts/qualify_ai.py" in text
    qualifier = text.index("python scripts/qualify_ai.py")
    assert qualifier < text.index("validate_core v1.19.30")
    assert qualifier < text.index("validate_core v1.19.29")


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
