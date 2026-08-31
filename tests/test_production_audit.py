from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from clash_relay.config_loader import load_project
from clash_relay.errors import ValidationError
from clash_relay.production_audit import (
    audit_production_candidate,
    render_production_summary_markdown,
)


def _project(project_paths):
    return load_project(**project_paths)


def _group(candidate, name: str) -> dict:
    return next(item for item in candidate["proxy-groups"] if item["name"] == name)


def _with_acl_surfaces(project, candidate):
    manifest = {
        "groups": [
            {
                "id": "policy_youtube",
                "display_name": "YouTube",
                "module": "general",
                "members": [],
            },
            {
                "id": "policy_final",
                "display_name": "Final",
                "module": "general",
                "members": [],
            },
        ],
        "sources": [
            {
                "id": "youtube",
                "target": "YouTube",
                "module": "general",
            }
        ],
        "inline_rules": [],
        "final_target": "Final",
        "final_source_use": "general",
    }
    candidate["proxy-groups"].extend(
        [
            {"name": "YouTube", "type": "select", "proxies": ["Proxy"]},
            {"name": "Final", "type": "select", "proxies": ["Proxy"]},
        ]
    )
    candidate["rules"] = ["RULE-SET,acl4ssr_youtube,YouTube", "MATCH,Final"]
    return replace(project, acl4ssr=manifest)


def test_production_audit_reports_only_aggregate_source_counts(
    built_candidate, project_paths
) -> None:
    summary = audit_production_candidate(
        _project(project_paths),
        built_candidate.config,
        build_report=built_candidate.report,
    )

    assert summary["status"] == "passed"
    pools = {item["id"]: item for item in summary["pools"]}
    assert pools["general"]["source_use"] == "general"
    assert pools["general"]["nodes"] > 0
    assert "special" not in pools["general"]["sources"]
    assert summary["reachability"]["status"] == "passed"
    assert summary["reachability"]["groups_checked"] > 0

    markdown = render_production_summary_markdown(summary)
    assert "Fictional General" not in markdown
    assert "example.invalid" not in markdown
    assert "Source-use policy: **passed**" in markdown
    assert "Routing graph reachability: **passed**" in markdown


def test_production_audit_checks_declared_and_runtime_acl_surfaces(
    built_candidate, project_paths
) -> None:
    candidate = copy.deepcopy(built_candidate.config)
    project = _with_acl_surfaces(_project(project_paths), candidate)

    summary = audit_production_candidate(project, candidate)

    assert summary["reachability"]["routing_surfaces_checked"] == 2
    assert summary["reachability"]["runtime_rules_checked"] == 2


def test_production_audit_fails_when_source_enters_disallowed_pool(
    built_candidate, project_paths
) -> None:
    candidate = copy.deepcopy(built_candidate.config)
    providers = candidate["proxy-providers"]
    general = providers["cr_general_any"]["payload"]
    residential = providers["cr_residential_any"]["payload"]
    general.append(copy.deepcopy(residential[0]))

    with pytest.raises(ValidationError, match="source-use boundary violated"):
        audit_production_candidate(_project(project_paths), candidate)


def test_production_audit_fails_when_general_group_can_reach_restricted_pool(
    built_candidate, project_paths
) -> None:
    candidate = copy.deepcopy(built_candidate.config)
    project = _with_acl_surfaces(_project(project_paths), candidate)
    youtube = _group(candidate, "YouTube")
    youtube["proxies"].append("Residential")

    with pytest.raises(ValidationError, match="routing reachability boundary violated"):
        audit_production_candidate(project, candidate)


def test_production_audit_fails_when_ruleset_is_retargeted_to_restricted_pool(
    built_candidate, project_paths
) -> None:
    candidate = copy.deepcopy(built_candidate.config)
    project = _with_acl_surfaces(_project(project_paths), candidate)
    candidate["rules"][0] = "RULE-SET,acl4ssr_youtube,Residential"

    with pytest.raises(ValidationError, match="routing reachability boundary violated"):
        audit_production_candidate(project, candidate)


def test_production_audit_fails_when_final_fallback_can_reach_restricted_pool(
    built_candidate, project_paths
) -> None:
    candidate = copy.deepcopy(built_candidate.config)
    project = _with_acl_surfaces(_project(project_paths), candidate)
    final_group = _group(candidate, "Final")
    final_group["proxies"].append("Residential")

    with pytest.raises(ValidationError, match="routing reachability boundary violated"):
        audit_production_candidate(project, candidate)


def test_production_audit_includes_multiplier_filter_counts(built_candidate, project_paths) -> None:
    report = copy.deepcopy(built_candidate.report)
    primary = next(item for item in report["subscriptions"] if item["id"] == "primary")
    primary["filtered_over_multiplier"] = 3
    summary = audit_production_candidate(
        _project(project_paths),
        built_candidate.config,
        build_report=report,
    )
    by_source = {item["id"]: item for item in summary["subscriptions"]}
    assert by_source["primary"]["filtered_over_multiplier"] == 3
