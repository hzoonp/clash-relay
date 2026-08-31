from __future__ import annotations

from clash_relay.config_loader import load_project
from clash_relay.routing_model import compile_routing_model
from clash_relay.routing_policy_v2 import load_routing_policy_v2
from clash_relay.routing_shadow import routing_drift_summary


def _project(repo_root):
    return load_project(
        config_path=repo_root / "config.yaml",
        subscriptions_path=repo_root / "subscriptions.yaml",
        services_path=repo_root / "services.yaml",
        policies_path=repo_root / "policies.yaml",
    )


def test_complex_concurrent_scenarios_have_independent_route_intent(
    repo_root,
) -> None:
    project = _project(repo_root)
    model = compile_routing_model(project.acl4ssr)
    assert model is not None
    rows = {row["source_id"]: row for row in model["bindings"]}

    expected = {
        "china_domain": ("direct", "domestic_web", "全球直连"),
        "china_company_ip": ("direct", "domestic_web", "全球直连"),
        "proxy_lite": ("browsing", "foreign_web", "网页浏览"),
        "telegram": ("general", "telegram", "消息通讯"),
        "proxy_media": ("media", "foreign_media", "流媒体"),
        "download": ("download", "download", "下载流量"),
        "openai": ("ai", "openai", "人工智能"),
        "ai": ("ai", "generic_ai", "人工智能"),
        "__final__": ("final", None, "漏网之鱼"),
    }
    for source_id, (scenario, service, target) in expected.items():
        row = rows[source_id]
        assert row["scenario"] == scenario
        assert row.get("service") == service
        assert row["target"] == target

    for removed in (
        "proxy_gfwlist",
        "youtube",
        "netflix",
        "bilibili",
        "china_media",
    ):
        assert removed not in rows


def test_scenario_permissions_prevent_browsing_source_from_media_download_and_final(
    repo_root,
) -> None:
    project = _project(repo_root)
    policy = load_routing_policy_v2(project.policies)
    subscription_1 = next(item for item in project.subscriptions if item.id == "subscription_1")

    assert subscription_1.allowed_uses == frozenset({"browsing", "ai"})
    allowed_scenarios = {
        scenario
        for scenario in ("direct", "general", "browsing", "media", "download", "ai", "final")
        if policy.scenario_use(scenario) in subscription_1.allowed_uses
    }
    assert allowed_scenarios == {"browsing", "ai"}


def test_acl4ssr_baseline_extensions_have_explicit_order(repo_root) -> None:
    project = _project(repo_root)
    model = compile_routing_model(project.acl4ssr)
    assert model is not None
    rows = {row["source_id"]: row for row in model["bindings"]}

    assert rows["telegram"]["priority"] < rows["ai"]["priority"] < rows["proxy_media"]["priority"]
    assert (
        rows["telegram"]["priority"] < rows["openai"]["priority"] < rows["proxy_media"]["priority"]
    )
    assert (
        rows["proxy_media"]["priority"]
        < rows["download"]["priority"]
        < rows["proxy_lite"]["priority"]
        < rows["china_domain"]["priority"]
        < rows["china_company_ip"]["priority"]
        < rows["geoip_cn"]["priority"]
    )


def test_finalized_routing_v2_graph_has_no_declared_drift(repo_root) -> None:
    drift = routing_drift_summary(_project(repo_root))

    assert drift["status"] == "healthy"
    assert drift["foreign_web"] == {
        "explicit_rule_sources": 1,
        "classifier": "ProxyLite",
        "classifier_widened": False,
        "policy_applied": True,
    }
    assert drift["download"] == {
        "mode": "general_auto",
        "rule_sources": 1,
        "scheduler_applied": True,
    }
    assert drift["media"] == {
        "rule_sources": 1,
        "classifier": "ProxyMedia",
        "scheduler_applied": True,
    }
    assert drift["messaging"] == {
        "rule_sources": 1,
        "classifier": "Telegram",
        "scheduler_applied": True,
    }
    assert drift["acl4ssr_fidelity"] == {
        "compatibility_selectors_applied": True,
        "classification_order_applied": True,
        "ban_program_ad_disabled": True,
        "intentional_extensions": ["ai", "openai", "download"],
    }
    assert drift["ai"] == {
        "region_order": ["US", "SG", "JP", "TW", "KR", "OTHER"],
        "declared_region_order": ["US", "SG", "JP", "TW", "KR", "OTHER"],
        "excluded_regions": ["HK"],
        "policy_applied": True,
    }
