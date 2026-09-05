from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path

from clash_relay.config_loader import load_project
from clash_relay.promotion_guard import (
    PromotionGuardPolicy,
    assess_promotion,
    load_promotion_guard_policy,
)

ROOT = Path(__file__).resolve().parents[1]


def _project(project_paths):
    return load_project(**project_paths)


def _canonical_policy() -> PromotionGuardPolicy:
    return load_promotion_guard_policy(ROOT / "promotion-guard.yaml")


def _fixture_policy() -> PromotionGuardPolicy:
    policy = _canonical_policy()
    return replace(
        policy,
        minimum_source_ratio_by_use={"general": policy.minimum_source_ratio_by_use["general"]},
        minimum_sources_by_use={"general": 1},
        minimum_nodes_by_use={"general": 1},
        minimum_regions_by_use={"general": 1},
    )


def test_canonical_promotion_guard_requires_all_public_scenario_uses() -> None:
    policy = _canonical_policy()
    required = {"general", "browsing", "ai"}

    assert set(policy.minimum_source_ratio_by_use) == required
    assert set(policy.minimum_sources_by_use) == required
    assert set(policy.minimum_nodes_by_use) == required
    assert set(policy.minimum_regions_by_use) == required


def test_promotion_guard_allows_first_release(built_candidate, project_paths) -> None:
    report = assess_promotion(
        _project(project_paths),
        built_candidate.config,
        None,
        _fixture_policy(),
    )

    assert report["status"] == "passed"
    assert report["reason"] == "first_release"
    assert all(item["regions"] >= 1 for item in report["candidate"]["uses"].values())


def test_promotion_guard_blocks_first_release_without_required_availability(
    built_candidate, project_paths
) -> None:
    candidate = copy.deepcopy(built_candidate.config)
    for provider in candidate["proxy-providers"].values():
        provider["payload"] = []

    report = assess_promotion(
        _project(project_paths),
        candidate,
        None,
        _fixture_policy(),
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "availability_contract"
    assert any(str(item).startswith("minimum_nodes:") for item in report["violations"])
    assert any(str(item).startswith("minimum_regions:") for item in report["violations"])


def test_promotion_guard_blocks_severe_inventory_collapse(built_candidate, project_paths) -> None:
    baseline = copy.deepcopy(built_candidate.config)
    candidate = copy.deepcopy(built_candidate.config)
    for provider in candidate["proxy-providers"].values():
        provider["payload"] = []

    report = assess_promotion(
        _project(project_paths),
        candidate,
        baseline,
        _fixture_policy(),
    )

    assert report["status"] == "blocked"
    assert "total_node_ratio" in report["violations"]
    assert any(str(item).startswith("minimum_sources:") for item in report["violations"])
    assert any(str(item).startswith("minimum_nodes:") for item in report["violations"])
    assert any(str(item).startswith("minimum_regions:") for item in report["violations"])
    assert report["candidate"]["nodes"] == 0
    assert report["baseline"]["nodes"] > 0


def test_promotion_guard_report_is_aggregate_only(built_candidate, project_paths) -> None:
    report = assess_promotion(
        _project(project_paths),
        built_candidate.config,
        copy.deepcopy(built_candidate.config),
        _fixture_policy(),
    )

    rendered = repr(report)
    assert report["status"] == "passed"
    assert "regions" in rendered
    assert "example.invalid" not in rendered
    assert "password" not in rendered.lower()
