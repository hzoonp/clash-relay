from __future__ import annotations

import copy
from pathlib import Path

from clash_relay.config_loader import load_project
from clash_relay.promotion_guard import assess_promotion, load_promotion_guard_policy

ROOT = Path(__file__).resolve().parents[1]


def _project(project_paths):
    return load_project(**project_paths)


def test_promotion_guard_allows_first_release(built_candidate, project_paths) -> None:
    report = assess_promotion(
        _project(project_paths),
        built_candidate.config,
        None,
        load_promotion_guard_policy(ROOT / "promotion-guard.yaml"),
    )

    assert report["status"] == "passed"
    assert report["reason"] == "first_release"


def test_promotion_guard_blocks_severe_inventory_collapse(built_candidate, project_paths) -> None:
    baseline = copy.deepcopy(built_candidate.config)
    candidate = copy.deepcopy(built_candidate.config)
    for provider in candidate["proxy-providers"].values():
        provider["payload"] = []

    report = assess_promotion(
        _project(project_paths),
        candidate,
        baseline,
        load_promotion_guard_policy(ROOT / "promotion-guard.yaml"),
    )

    assert report["status"] == "blocked"
    assert "total_node_ratio" in report["violations"]
    assert any(str(item).startswith("minimum_sources:") for item in report["violations"])
    assert report["candidate"]["nodes"] == 0
    assert report["baseline"]["nodes"] > 0


def test_promotion_guard_report_is_aggregate_only(built_candidate, project_paths) -> None:
    report = assess_promotion(
        _project(project_paths),
        built_candidate.config,
        copy.deepcopy(built_candidate.config),
        load_promotion_guard_policy(ROOT / "promotion-guard.yaml"),
    )

    rendered = repr(report)
    assert report["status"] == "passed"
    assert "example.invalid" not in rendered
    assert "password" not in rendered.lower()
