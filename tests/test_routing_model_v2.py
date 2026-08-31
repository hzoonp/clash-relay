from __future__ import annotations

from pathlib import Path

import yaml

from clash_relay.acl4ssr_policy import apply_acl4ssr_group_semantics
from clash_relay.routing_model import compile_routing_model


def _manifest(repo_root: Path) -> dict:
    with (repo_root / "rules" / "acl4ssr.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_routing_model_v2_classifies_complex_scenarios(repo_root: Path) -> None:
    report = compile_routing_model(_manifest(repo_root))
    assert report is not None
    bindings = {row["source_id"]: row for row in report["bindings"]}

    assert bindings["proxy_gfwlist"]["scenario"] == "browsing"
    assert bindings["proxy_gfwlist"]["service"] == "foreign_web"
    assert bindings["proxy_gfwlist"]["source_use"] == "browsing"

    assert bindings["youtube"]["scenario"] == "media"
    assert bindings["youtube"]["service"] == "youtube"
    assert bindings["netflix"]["scenario"] == "media"
    assert bindings["netflix"]["service"] == "netflix"
    assert bindings["download"]["scenario"] == "download"

    assert bindings["ai"]["scenario"] == "ai"
    assert bindings["ai"]["source_use"] == "ai"
    assert bindings["openai"]["scenario"] == "ai"
    assert bindings["openai"]["service"] == "openai"
    assert bindings["__final__"]["scenario"] == "final"
    assert bindings["__final__"]["target"] == "漏网之鱼"


def test_public_scenario_selectors_are_explicit_and_general_only_where_required(
    repo_root: Path,
) -> None:
    manifest = _manifest(repo_root)
    groups = {row["display_name"]: row for row in manifest["groups"]}
    visible = {name for name, row in groups.items() if not bool(row.get("hidden", False))}

    assert visible == {
        "代理选择",
        "网页浏览",
        "人工智能",
        "流媒体",
        "消息通讯",
        "下载流量",
    }
    assert groups["流媒体"]["members"][0] == {"group": "媒体自动"}
    assert groups["消息通讯"]["members"][0] == {"group": "通讯自动"}
    assert groups["下载流量"]["members"][0] == {"group": "下载自动"}
    assert groups["媒体自动"]["provider_pool"] == "general"
    assert groups["通讯自动"]["provider_pool"] == "general"
    assert groups["下载自动"]["provider_pool"] == "general"


def test_internal_rule_targets_are_deterministic(repo_root: Path) -> None:
    manifest = _manifest(repo_root)
    targets = {
        row["display_name"]: row["route"]
        for row in manifest["groups"]
        if isinstance(row.get("route"), dict)
    }

    expected = {
        "全球直连": "DIRECT",
        "广告拦截": "REJECT",
        "谷歌FCM": "DIRECT",
        "微软Bing": "DIRECT",
        "微软云盘": "DIRECT",
        "微软服务": "DIRECT",
        "苹果服务": "DIRECT",
        "电报消息": "消息通讯",
        "网易音乐": "DIRECT",
        "游戏平台": "DIRECT",
        "油管视频": "流媒体",
        "巴哈姆特": "台湾节点",
        "哔哩哔哩": "DIRECT",
        "国内媒体": "DIRECT",
        "国外媒体": "流媒体",
        "漏网之鱼": "代理选择",
    }
    assert "应用净化" not in targets
    for name, destination in expected.items():
        route = targets[name]
        assert route["deterministic"] is True
        member = route["member"]
        assert member.get("builtin", member.get("group")) == destination

    # Public selectors are user-controlled and therefore never deterministic.
    for name in ("流媒体", "消息通讯", "下载流量"):
        assert targets[name].get("deterministic", False) is False

    # Netflix is automatic failover, not persisted hidden user selection.
    netflix = next(row for row in manifest["groups"] if row["display_name"] == "奈飞视频")
    assert netflix["type"] == "fallback"
    assert netflix["route"]["scenario"] == "media"
    assert netflix["route"]["service"] == "netflix"
    assert netflix["members"] == [{"group": "奈飞节点"}, {"group": "流媒体"}]


def test_policy_postprocessor_reduces_deterministic_target_to_one_hop() -> None:
    output = {
        "proxy-providers": {},
        "proxy-groups": [
            {"name": "代理选择", "type": "select", "proxies": ["DIRECT"]},
            {
                "name": "油管视频",
                "type": "select",
                "proxies": ["代理选择", "DIRECT"],
            },
        ],
    }
    report = apply_acl4ssr_group_semantics(
        output,
        group_specs=[
            {
                "id": "policy_youtube",
                "display_name": "油管视频",
                "hidden": True,
                "members": [{"group": "代理选择"}, {"builtin": "DIRECT"}],
                "route": {
                    "scenario": "media",
                    "service": "youtube",
                    "deterministic": True,
                    "member": {"group": "代理选择"},
                },
            }
        ],
        pool_specs=[],
    )

    youtube = output["proxy-groups"][1]
    assert youtube == {
        "name": "油管视频",
        "type": "select",
        "hidden": True,
        "proxies": ["代理选择"],
    }
    assert report["deterministic_routes"] == ["油管视频"]