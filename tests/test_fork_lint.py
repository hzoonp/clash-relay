from __future__ import annotations

import json
from pathlib import Path

from clash_relay.config_loader import load_project
from clash_relay.doctor import run_doctor
from clash_relay.fork_lint import build_fork_lint


def _paths(repo_root: Path) -> dict[str, Path]:
    return {
        "config_path": repo_root / "config.yaml",
        "subscriptions_path": repo_root / "subscriptions.yaml",
        "policies_path": repo_root / "policies.yaml",
        "mihomo_manifest": repo_root / "tools/mihomo-versions.json",
    }


def _private_env() -> dict[str, str]:
    return {
        "CLASH_RELAY_SUBSCRIPTIONS": json.dumps(
            {
                "SUBSCRIPTION_1_URL": "https://secret-one.example/sub",
                "SUBSCRIPTION_2_URL": "https://secret-two.example/sub",
                "SUBSCRIPTION_3_URL": "https://secret-three.example/sub",
                "SUBSCRIPTION_4_URL": "https://secret-four.example/sub",
            }
        )
    }


def test_canonical_fork_lint_exposes_source_policy_boundaries(repo_root: Path) -> None:
    project = load_project(
        config_path=repo_root / "config.yaml",
        subscriptions_path=repo_root / "subscriptions.yaml",
        policies_path=repo_root / "policies.yaml",
    )

    report = build_fork_lint(project)

    assert report["status"] == "passed"
    assert report["enabled_sources"] == 4
    assert report["sources_by_use"] == {"general": 3, "browsing": 4, "ai": 4}
    assert report["restricted_non_general_sources"] == 1
    assert report["multiplier_capped_sources"] == 1
    assert report["deny_filtered_sources"] == 1
    sub1 = next(item for item in report["sources"] if item["id"] == "subscription_1")
    assert sub1["allowed_uses"] == ["ai", "browsing"]
    assert sub1["max_node_multiplier"] == 2.0
    assert sub1["deny_name_pattern_count"] == 1


def test_public_doctor_embeds_lint_and_expected_secret_names_only(repo_root: Path) -> None:
    report = run_doctor(**_paths(repo_root), public_only=True, env={})
    serialized = json.dumps(report, sort_keys=True)

    assert report["fork_lint"]["status"] == "passed"
    assert report["fork_lint"]["secrets"] == {
        "status": "not_checked",
        "expected_names": [
            "SUBSCRIPTION_1_URL",
            "SUBSCRIPTION_2_URL",
            "SUBSCRIPTION_3_URL",
            "SUBSCRIPTION_4_URL",
        ],
    }
    assert report["fork_lint"]["dry_run"]["publication_default"] is False
    assert "secret-one.example" not in serialized


def test_private_doctor_reports_secret_presence_without_values(repo_root: Path) -> None:
    report = run_doctor(**_paths(repo_root), env=_private_env())
    serialized = json.dumps(report, sort_keys=True)

    assert report["fork_lint"]["secrets"] == {
        "status": "ready",
        "expected_names": [
            "SUBSCRIPTION_1_URL",
            "SUBSCRIPTION_2_URL",
            "SUBSCRIPTION_3_URL",
            "SUBSCRIPTION_4_URL",
        ],
        "resolved": 4,
        "missing": [],
    }
    assert "secret-one.example" not in serialized
    assert "secret-two.example" not in serialized
