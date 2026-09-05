from __future__ import annotations

import json
from pathlib import Path

from clash_relay.doctor import run_doctor


def _paths(repo_root: Path) -> dict[str, Path]:
    return {
        "config_path": repo_root / "config.yaml",
        "subscriptions_path": repo_root / "subscriptions.yaml",
        "policies_path": repo_root / "policies.yaml",
        "mihomo_manifest": repo_root / "tools/mihomo-versions.json",
    }


def test_public_doctor_explains_current_policy_model_and_first_publish_path(
    repo_root: Path,
) -> None:
    report = run_doctor(**_paths(repo_root), public_only=True, env={})

    assert report["public"]["policy_model_version"] == 2
    assert report["public"]["policy_model_status"] == "current"
    assert report["public"]["service_qualification_status"] == "ready"
    assert report["public"]["service_qualification_probes"] == 3
    assert report["guidance"]["first_publish_default"] is False
    assert report["guidance"]["enabled_subscription_secrets"] == [
        "SUBSCRIPTION_1_URL",
        "SUBSCRIPTION_2_URL",
        "SUBSCRIPTION_3_URL",
        "SUBSCRIPTION_4_URL",
    ]
    guidance = " ".join(report["guidance"]["next_steps"])
    assert "publish=false" in guidance
    assert "CLASH_RELAY_SUBSCRIPTIONS" in guidance


def test_public_doctor_guidance_contains_no_secret_values(repo_root: Path) -> None:
    report = run_doctor(**_paths(repo_root), public_only=True, env={})
    serialized = json.dumps(report, sort_keys=True)

    assert "https://" not in serialized
    assert "CLOUDFLARE_API_TOKEN" not in serialized
    assert "CLOUDFLARE_ACCOUNT_ID" not in serialized
