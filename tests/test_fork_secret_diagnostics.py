from __future__ import annotations

import json

import pytest

from clash_relay.config_loader import load_project
from clash_relay.errors import SecretError
from clash_relay.secrets import inspect_subscription_secret_names, resolve_subscription_urls


def _project(repo_root):
    return load_project(
        config_path=repo_root / "config.yaml",
        subscriptions_path=repo_root / "subscriptions.yaml",
        policies_path=repo_root / "policies.yaml",
    )


def test_secret_name_preflight_reports_missing_names_without_values(repo_root) -> None:
    project = _project(repo_root)
    env = {
        "CLASH_RELAY_SUBSCRIPTIONS": json.dumps(
            {
                "SUBSCRIPTION_1_URL": "https://private-one.example/sub",
            }
        )
    }

    report = inspect_subscription_secret_names(list(project.subscriptions), env=env)
    serialized = json.dumps(report, sort_keys=True)

    assert report["status"] == "missing"
    assert report["category"] == "missing_subscription_secrets"
    assert report["resolved"] == 1
    assert report["expected"] == 5
    assert report["missing"] == [
        "SUBSCRIPTION_2_URL",
        "SUBSCRIPTION_3_URL",
        "SUBSCRIPTION_4_URL",
        "SUBSCRIPTION_5_URL",
    ]
    assert "private-one.example" not in serialized


def test_secret_name_preflight_reports_ready_counts_only(repo_root) -> None:
    project = _project(repo_root)
    env = {
        name: f"https://private-{index}.example/sub"
        for index, name in enumerate(
            [
                "SUBSCRIPTION_1_URL",
                "SUBSCRIPTION_2_URL",
                "SUBSCRIPTION_3_URL",
                "SUBSCRIPTION_4_URL",
                "SUBSCRIPTION_5_URL",
            ],
            start=1,
        )
    }

    report = inspect_subscription_secret_names(list(project.subscriptions), env=env)

    assert report["status"] == "ready"
    assert report["category"] is None
    assert report["resolved"] == report["expected"] == 5
    assert report["missing"] == []


def test_resolver_missing_error_has_stable_category_and_progress(repo_root) -> None:
    project = _project(repo_root)
    env = {
        "SUBSCRIPTION_1_URL": "https://private-one.example/sub",
        "SUBSCRIPTION_2_URL": "https://private-two.example/sub",
    }

    with pytest.raises(SecretError) as caught:
        resolve_subscription_urls(list(project.subscriptions), env=env)

    message = str(caught.value)
    assert "category=missing_subscription_secrets" in message
    assert "resolved=2/5" in message
    assert "SUBSCRIPTION_3_URL" in message
    assert "SUBSCRIPTION_4_URL" in message
    assert "SUBSCRIPTION_5_URL" in message
    assert "private-one.example" not in message
    assert "private-two.example" not in message
