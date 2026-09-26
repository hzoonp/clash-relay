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

    preflight = collector["steps"][4]
    assert preflight["name"] == "Validate, merge, and preflight aggregate evidence"
    assert preflight["env"]["CLASH_RELAY_CARRIER_HMAC_KEY"] == HMAC_SECRET
    assert preflight["env"]["CLASH_RELAY_SUBSCRIPTIONS"] == SUBSCRIPTIONS_SECRET
    assert preflight["env"]["CLASH_RELAY_VALIDATED_SHA"] == VALIDATED_SHA
    assert '--carrier-qualification-input "$merged"' in preflight["run"]
    assert 'receipt=".work/carrier-observation-receipt.json"' in preflight["run"]
    assert 'test -s "$receipt"' in preflight["run"]
    assert "set -euo pipefail" in preflight["run"]
    # The canonical preflight reads production state with Cloudflare credentials,
    # while the lifecycle enforces zero writes before the explicit commit step.
    assert preflight["env"]["CLOUDFLARE_API_TOKEN"] == "${{ secrets.CLOUDFLARE_API_TOKEN }}"
    assert "persist_carrier_observation.py" not in preflight["run"]

    commit = collector["steps"][5]
    assert commit["name"] == "Persist carrier observation history"
    assert commit["if"] == "success()"
    assert commit["env"]["CLOUDFLARE_API_TOKEN"] == "${{ secrets.CLOUDFLARE_API_TOKEN }}"
    assert commit["env"]["CLASH_RELAY_CARRIER_HMAC_KEY"] == HMAC_SECRET
    assert commit["env"]["CLASH_RELAY_VALIDATED_SHA"] == VALIDATED_SHA
    assert "set -euo pipefail" in commit["run"]
    assert (
        "persist_carrier_observation.py --root . --receipt .work/carrier-observation-receipt.json"
        in commit["run"]
    )
    # Phase B authenticates the receipt without needing raw subscriptions.
    assert "CLASH_RELAY_SUBSCRIPTIONS" not in commit["env"]
    cleanup = collector["steps"][6]
    assert cleanup["if"] == "always()"
    assert "carrier-observation-receipt.json" in cleanup["run"]
    assert len(collector["steps"]) == 7

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
