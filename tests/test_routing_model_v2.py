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

    assert bindings["proxy_lite"]["scenario"] == "browsing"
    assert bindings["proxy_lite"]["service"] == "foreign_web"
    assert bindings["proxy_lite"]["source_use"] == "browsing"

    assert bindings["proxy_media"]["scenario"] == "media"
    assert bindings["proxy_media"]["service"] == "foreign_media"
    assert bindings["telegram"]["scenario"] == "general"
    assert bindings["telegram"]["service"] == "telegram"
    assert bindings["download"]["scenario"] == "download"

    assert bindings["ai"]["scenario"] == "ai"
    assert bindings["ai"]["source_use"] == "ai"
    assert bindings["openai"]["scenario"] == "ai"
    assert bindings["openai"]["service"] == "openai"
    assert bindings["__final__"]["scenario"] == "final"
    assert bindings["__final__"]["target"] == "漏网之鱼"

    for removed in ("proxy_gfwlist", "youtube", "netflix", "bilibili", "china_media"):
        assert removed not in bindings


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


def test_acl4ssr_compatibility_selectors_preserve_reference_defaults(repo_root: Path) -> None:
    manifest = _manifest(repo_root)
    groups = {row["display_name"]: row for row in manifest["groups"]}

    expected = {
        "全球直连": [{"builtin": "DIRECT"}, {"group": "代理选择"}, {"group": "自动选择"}],
        "广告拦截": [{"builtin": "REJECT"}, {"builtin": "DIRECT"}],
        "谷歌FCM": [{"group": "代理选择"}, {"group": "全球直连"}, {"group": "自动选择"}],
        "微软服务": [{"group": "全球直连"}, {"group": "代理选择"}],
        "苹果服务": [{"group": "代理选择"}, {"group": "全球直连"}],
        "漏网之鱼": [{"group": "代理选择"}, {"group": "全球直连"}, {"group": "自动选择"}],
    }
    assert "应用净化" not in groups
    for name, members in expected.items():
        assert groups[name]["hidden"] is True
        assert groups[name]["type"] == "select"
        assert groups[name]["members"] == members
        assert groups[name].get("provider_pool") is None

    for removed in (
        "微软Bing",
        "微软云盘",
        "电报消息",
        "网易音乐",
        "游戏平台",
        "油管视频",
        "奈飞节点",
        "奈飞视频",
        "巴哈姆特",
        "哔哩哔哩",
        "国内媒体",
        "国外媒体",
    ):
        assert removed not in groups


def test_policy_postprocessor_reduces_deterministic_target_to_one_hop() -> None:
    output = {
        "proxy-providers": {},
        "proxy-groups": [
            {"name": "代理选择", "type": "select", "proxies": ["DIRECT"]},
            {
                "name": "示例确定路由",
                "type": "select",
                "proxies": ["代理选择", "DIRECT"],
            },
        ],
    }
    report = apply_acl4ssr_group_semantics(
        output,
        group_specs=[
            {
                "id": "policy_example",
                "display_name": "示例确定路由",
                "hidden": True,
                "members": [{"group": "代理选择"}, {"builtin": "DIRECT"}],
                "route": {
                    "scenario": "media",
                    "service": "example",
                    "deterministic": True,
                    "member": {"group": "代理选择"},
                },
            }
        ],
        pool_specs=[],
    )

    example = output["proxy-groups"][1]
    assert example == {
        "name": "示例确定路由",
        "type": "select",
        "hidden": True,
        "proxies": ["代理选择"],
    }
    assert report["deterministic_routes"] == ["示例确定路由"]
