from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from clash_relay.config_loader import load_project
from clash_relay.errors import ValidationError
from clash_relay.routing_v2_audit import audit_routing_v2


def _project(repo_root):
    return load_project(
        config_path=repo_root / "config.yaml",
        subscriptions_path=repo_root / "subscriptions.yaml",
        services_path=repo_root / "services.yaml",
        policies_path=repo_root / "policies.yaml",
    )


def _member(route: dict, project) -> str:
    member = route["member"]
    if "builtin" in member:
        return str(member["builtin"])
    if "group" in member:
        return str(member["group"])
    pool_id = str(member["auto_pool"])
    pool = next(row for row in project.policies["pools"] if row["id"] == pool_id)
    return str(pool["display_name"])


def _candidate(project) -> dict:
    groups = [
        {"name": "代理选择", "type": "select", "proxies": ["DIRECT"]},
        {"name": "网页浏览", "type": "select", "proxies": ["DIRECT"]},
        {"name": "人工智能", "type": "select", "proxies": ["DIRECT"]},
    ]
    for spec in project.acl4ssr["groups"]:
        route = spec.get("route")
        if not isinstance(route, dict) or route.get("deterministic") is not True:
            continue
        groups.append(
            {
                "name": str(spec["display_name"]),
                "type": "select",
                "hidden": True,
                "proxies": [_member(route, project)],
            }
        )
    groups.extend(
        [
            {
                "name": "媒体自动",
                "type": "url-test",
                "hidden": True,
                "use": ["fixture_general"],
            },
            {
                "name": "下载自动",
                "type": "url-test",
                "hidden": True,
                "use": ["fixture_general"],
            },
            {
                "name": "奈飞视频",
                "type": "fallback",
                "hidden": True,
                "proxies": ["奈飞节点", "媒体自动"],
            },
        ]
    )
    return {"proxy-groups": groups}


def _group(candidate: dict, name: str) -> dict:
    return next(row for row in candidate["proxy-groups"] if row["name"] == name)


def test_routing_v2_audit_accepts_prequalification_graph(repo_root) -> None:
    project = _project(repo_root)
    summary = audit_routing_v2(project, _candidate(project))

    assert summary["status"] == "passed"
    assert summary["model_version"] == 2
    assert summary["bindings_checked"] > 20
    assert summary["deterministic_targets_checked"] > 10
    assert summary["ai"]["stage"] == "pre_qualification"
    assert summary["ai"]["excluded_regions"] == ["HK"]


def test_routing_v2_audit_accepts_complete_fail_closed_ai_service_set(repo_root) -> None:
    project = _project(repo_root)
    candidate = _candidate(project)
    for service in ("OPENAI", "CLAUDE", "GEMINI"):
        candidate["proxy-groups"].append(
            {
                "name": f"__CR_AI_SERVICE_{service}",
                "type": "select",
                "hidden": True,
                "proxies": ["REJECT"],
            }
        )

    summary = audit_routing_v2(project, candidate)

    assert summary["ai"]["stage"] == "post_qualification"
    assert summary["ai"]["service_targets_checked"] == 3
    assert summary["visible_groups"] == 3


def test_routing_v2_audit_rejects_persisted_hidden_selector_state(repo_root) -> None:
    project = _project(repo_root)
    candidate = _candidate(project)
    _group(candidate, "油管视频")["proxies"] = ["代理选择", "DIRECT"]

    with pytest.raises(ValidationError, match="not a hidden one-hop route"):
        audit_routing_v2(project, candidate)


def test_routing_v2_audit_rejects_source_use_drift(repo_root) -> None:
    project = _project(repo_root)
    manifest = copy.deepcopy(project.acl4ssr)
    youtube = next(row for row in manifest["sources"] if row["id"] == "youtube")
    youtube["source_use"] = "browsing"
    drifted = replace(project, acl4ssr=manifest)

    with pytest.raises(ValidationError, match="source-use contract mismatch"):
        audit_routing_v2(drifted, _candidate(drifted))


def test_routing_v2_audit_rejects_hk_ai_pool(repo_root) -> None:
    project = _project(repo_root)
    policies = copy.deepcopy(project.policies)
    hk_pool = copy.deepcopy(next(row for row in policies["pools"] if row["id"] == "ai_sg"))
    hk_pool.update(
        {
            "id": "ai_hk",
            "display_name": "AI · 香港",
            "regions": ["HK"],
            "fallback_order": ["HK"],
        }
    )
    policies["pools"].append(hk_pool)
    drifted = replace(project, policies=policies)

    with pytest.raises(ValidationError, match="materializes an excluded region"):
        audit_routing_v2(drifted, _candidate(drifted))


def test_routing_v2_audit_rejects_cross_service_ai_anchor(repo_root) -> None:
    project = _project(repo_root)
    candidate = _candidate(project)
    candidate["proxy-groups"].extend(
        [
            {
                "name": "__CR_AI_CLAUDE_BAD",
                "type": "url-test",
                "hidden": True,
                "proxies": ["DIRECT"],
            },
            {
                "name": "__CR_AI_SERVICE_OPENAI",
                "type": "select",
                "hidden": True,
                "proxies": ["__CR_AI_CLAUDE_BAD"],
            },
            {
                "name": "__CR_AI_SERVICE_CLAUDE",
                "type": "select",
                "hidden": True,
                "proxies": ["REJECT"],
            },
            {
                "name": "__CR_AI_SERVICE_GEMINI",
                "type": "select",
                "hidden": True,
                "proxies": ["REJECT"],
            },
        ]
    )

    with pytest.raises(ValidationError, match="non-service-qualified anchor"):
        audit_routing_v2(project, candidate)
