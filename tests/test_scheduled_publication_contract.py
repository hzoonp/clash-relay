from __future__ import annotations

from pathlib import Path

import pytest

from clash_relay.errors import ValidationError
from clash_relay.publication_decision import PublicationMode, resolve_publication_decision

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"
RUNNER = ROOT / "scripts" / "run_production_release.py"
RELEASE_STAGE = ROOT / "src" / "clash_relay" / "production_release_stage.py"


def test_authorized_schedule_reaches_publish_but_push_never_does() -> None:
    scheduled = resolve_publication_decision(
        event_name="schedule",
        scheduled_publish="true",
    )
    assert scheduled.mode is PublicationMode.PUBLISH
    assert scheduled.reason == "scheduled_publication"

    push = resolve_publication_decision(
        explicit_publish=True,
        event_name="push",
        manual_publish=True,
        scheduled_publish=True,
    )
    assert push.mode is PublicationMode.DRY_RUN
    assert push.should_publish is False


def test_schedule_gate_is_fail_closed_when_missing_disabled_or_ambiguous() -> None:
    for value in (None, "", False, "false"):
        assert (
            resolve_publication_decision(
                event_name="schedule",
                scheduled_publish=value,
            ).mode
            is PublicationMode.DRY_RUN
        )

    with pytest.raises(ValidationError, match="scheduled publication enablement"):
        resolve_publication_decision(
            event_name="schedule",
            scheduled_publish="enabled",
        )


def test_workflow_keeps_schedule_publication_inside_exact_sha_serialized_gate() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert '    - cron: "17 */6 * * *"' in text
    assert "CLASH_RELAY_SCHEDULE_PUBLISH:" in text
    assert "github.event_name == 'schedule'" in text
    assert "vars.CLASH_RELAY_SCHEDULE_PUBLISH == 'true'" in text
    assert "github.repository == 'hzoonp/clash-relay'" in text
    assert "vars.CLASH_RELAY_SCHEDULE_PUBLISH == ''" in text
    assert "needs.validate.outputs.validated_sha == github.sha" in text
    assert "ref: ${{ needs.validate.outputs.validated_sha }}" in text
    assert "cancel-in-progress: false" in text
    assert text.count("python scripts/run_production_release.py") == 1
    assert "actions/upload-artifact" not in text
    assert "gh release" not in text
    assert "publish-gist" not in text


def test_canonical_runner_enforces_exact_sha_for_every_ci_publication() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert 'os.environ.get("CLASH_RELAY_SCHEDULE_PUBLISH")' in text
    assert 'os.environ.get("GITHUB_ACTIONS", "").lower() != "true"' in text
    assert 'os.environ.get("GITHUB_SHA", "").strip()' in text
    assert 'os.environ.get("CLASH_RELAY_VALIDATED_SHA", "").strip()' in text
    assert "github_sha != validated_sha" in text
    assert "CI publication requires the exact validated commit SHA" in text
    assert "audit_production_event_result(result, decision)" in text


def test_scheduled_publish_uses_the_same_guard_matrix_and_release_transaction() -> None:
    runner = RUNNER.read_text(encoding="utf-8")
    stage = RELEASE_STAGE.read_text(encoding="utf-8")

    assert runner.count("ProductionPipeline(") == 1
    assert "scheduled_publish=(" in runner
    assert "publish = decision.should_publish" in runner

    guard = stage.index("run_promotion_guard(")
    matrix = stage.index("validate_mihomo_matrix(")
    publish = stage.index("publish_production_release(")
    assert guard < matrix < publish
    assert stage.count("if publish:") == 2
