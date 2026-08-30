from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"


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
