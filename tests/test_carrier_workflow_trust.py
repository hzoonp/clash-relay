from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "carrier-probe.yml"
VALIDATED_SHA = "${{ needs.validate.outputs.validated_sha }}"


def test_carrier_workflow_runs_complete_ci_before_secret_bearing_jobs() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    assert jobs["validate"]["uses"] == "./.github/workflows/ci.yml"
    assert jobs["validate"]["permissions"] == {"contents": "read"}

    for carrier in ("telecom", "unicom", "mobile"):
        job = jobs[carrier]
        assert job["needs"] == "validate"
        assert "needs.validate.outputs.validated_sha == github.sha" in job["if"]
        assert job["runs-on"] == ["self-hosted", "linux", f"carrier-probe-{carrier}"]
        assert job["environment"] == f"carrier-probe-{carrier}"
        assert job["steps"][0]["with"]["ref"] == VALIDATED_SHA
        assert job["steps"][0]["with"]["persist-credentials"] is False
        assert job["steps"][1]["name"] == "Verify validated checkout identity"
        assert job["steps"][1]["env"]["VALIDATED_SHA"] == VALIDATED_SHA
        assert 'test "$VALIDATED_SHA" = "$GITHUB_SHA"' in job["steps"][1]["run"]
        assert 'test "$(git rev-parse HEAD)" = "$VALIDATED_SHA"' in job["steps"][1]["run"]
        assert job["steps"][3]["name"] == "Install runtime dependencies"
        assert "RUNNER_LABELS" not in job["steps"][-1]["env"]

    collector = jobs["collect"]
    assert set(collector["needs"]) == {"validate", "telecom", "unicom", "mobile"}
    assert "needs.validate.outputs.validated_sha == github.sha" in collector["if"]
    assert collector["steps"][0]["with"]["ref"] == VALIDATED_SHA
    assert collector["steps"][1]["name"] == "Verify validated checkout identity"
    assert collector["steps"][1]["env"]["VALIDATED_SHA"] == VALIDATED_SHA
    assert 'test "$VALIDATED_SHA" = "$GITHUB_SHA"' in collector["steps"][1]["run"]
    assert 'test "$(git rev-parse HEAD)" = "$VALIDATED_SHA"' in collector["steps"][1]["run"]
    assert collector["steps"][3]["name"] == "Install runtime dependencies"
    assert "actions/upload-artifact" not in WORKFLOW.read_text(encoding="utf-8")


def test_documented_scheduler_and_protected_environment_trust_boundary() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    for text in (english, chinese):
        assert "validated_sha" in text
        assert "runs-on" in text
        assert "carrier-probe-telecom" in text
        assert "carrier-probe-unicom" in text
        assert "carrier-probe-mobile" in text
