from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "carrier-probe.yml"
VALIDATED_SHA = "${{ needs.validate.outputs.validated_sha }}"
HMAC_SECRET = "${{ secrets.CLASH_RELAY_CARRIER_HMAC_KEY }}"
SUBSCRIPTIONS_SECRET = "${{ secrets.CLASH_RELAY_SUBSCRIPTIONS }}"


def test_collector_verifies_candidate_with_protected_repository_key() -> None:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    collector = jobs["collect"]

    assert collector["runs-on"] == "ubuntu-latest"
    assert collector["environment"] == "carrier-probe-collector"
    assert collector["steps"][0]["with"]["ref"] == VALIDATED_SHA
    assert collector["steps"][1]["name"] == "Verify validated checkout identity"
    assert 'test "$VALIDATED_SHA" = "$GITHUB_SHA"' in collector["steps"][1]["run"]
    assert 'test "$(git rev-parse HEAD)" = "$VALIDATED_SHA"' in collector["steps"][1]["run"]

    preflight = collector["steps"][-1]
    assert preflight["env"]["CLASH_RELAY_CARRIER_HMAC_KEY"] == HMAC_SECRET
    assert preflight["env"]["CLASH_RELAY_SUBSCRIPTIONS"] == SUBSCRIPTIONS_SECRET
    assert '--carrier-qualification-input "$merged"' in preflight["run"]

    for carrier in ("telecom", "unicom", "mobile"):
        job = jobs[carrier]
        assert job["runs-on"] == ["self-hosted", "linux", f"carrier-probe-{carrier}"]
        assert job["environment"] == f"carrier-probe-{carrier}"
        assert job["steps"][0]["with"]["ref"] == VALIDATED_SHA
        probe = job["steps"][-1]
        assert probe["env"]["CLASH_RELAY_CARRIER_HMAC_KEY"] == HMAC_SECRET
        assert probe["env"]["CLASH_RELAY_SUBSCRIPTIONS"] == SUBSCRIPTIONS_SECRET


def test_collector_key_and_candidate_binding_are_documented() -> None:
    for name in ("README.md", "README.zh-CN.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "carrier-probe-collector" in text
        assert "CLASH_RELAY_CARRIER_HMAC_KEY" in text
        assert "validated_sha" in text
        assert "inventory" in text
        assert "probe-plan" in text
