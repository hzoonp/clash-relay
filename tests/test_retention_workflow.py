from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "retention.yml"


def test_retention_is_manual_confirmed_main_only_and_serialized() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "  workflow_dispatch:\n" in text
    assert "  push:\n" not in text
    assert "      expected_plan_id:\n" in text
    assert "      confirm:\n" in text
    assert "        default: false\n" in text
    assert "github.ref == 'refs/heads/main' && inputs.confirm == true" in text
    assert "clash-relay-publish-${{ github.ref }}" in text


def test_retention_revalidates_exact_sha_and_reviewed_plan_before_deletion() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "uses: ./.github/workflows/ci.yml" in text
    assert "needs.validate.outputs.validated_sha == github.sha" in text
    assert "ref: ${{ needs.validate.outputs.validated_sha }}" in text
    assert 'test "$VALIDATED_SHA" = "$GITHUB_SHA"' in text
    assert "clash-relay plan-release-retention" in text
    assert "reviewed retention plan is stale" in text
    assert "clash-relay apply-release-retention" in text
    assert "--confirm-retention-delete" in text
    assert text.index("Recreate and verify the reviewed retention plan") < text.index(
        "Apply revalidated immutable release retention"
    )


def test_retention_keeps_plan_and_result_private() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert ".work/private/retention-plan.json" in text
    assert ".work/private/retention-result.json" in text
    assert "actions/upload-artifact" not in text
    assert "actions/download-artifact" not in text
    assert "rm -rf .work/private" in text
