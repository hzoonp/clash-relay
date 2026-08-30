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


def test_every_mutating_publish_stage_depends_on_the_configured_gate() -> None:
    text = WORKFLOW.read_text()
    assert "Publication skipped until config.yaml and subscriptions.yaml are committed" in text
    assert text.count("needs.prepare.outputs.configured == 'true'") >= 4
    assert "always()" not in text
    assert "continue-on-error" not in text
    assert "needs:\n      - prepare\n      - candidate\n      - validate" in text


def test_public_production_builds_fail_closed_before_secret_use() -> None:
    text = WORKFLOW.read_text()
    assert "REPOSITORY_PRIVATE: ${{ github.event.repository.private }}" in text
    assert 'if [[ "$REPOSITORY_PRIVATE" != "true" ]]' in text
    assert "Sensitive production build blocked" in text
    assert "refuses to read subscription Secrets" in text
    assert text.index("Sensitive production build blocked") < text.index(
        "CLASH_RELAY_SUBSCRIPTIONS"
    )


def test_individual_subscription_urls_are_masked_before_generation() -> None:
    text = WORKFLOW.read_text()
    assert "Mask individual subscription URLs" in text
    assert "from clash_relay.secrets import load_secret_mapping" in text
    assert 'print(f"::add-mask::{command_escape(value)}")' in text
    assert text.index("Mask individual subscription URLs") < text.index(
        "Generate and statically validate candidate"
    )


def test_sensitive_public_backends_are_disabled_by_default() -> None:
    config = yaml.safe_load(CONFIG_EXAMPLE.read_text())
    publishing = config["publishing"]
    assert publishing["github_release"]["enabled"] is False
    assert publishing["github_release"]["allow_sensitive_public_release"] is False
    assert publishing["gist"]["enabled"] is False
    assert publishing["gist"]["allow_sensitive_unlisted_gist"] is False
