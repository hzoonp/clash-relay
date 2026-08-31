from __future__ import annotations

from clash_relay.config_loader import load_project
from clash_relay.routing_model import compile_routing_model
from clash_relay.routing_policy_v2 import load_routing_policy_v2
from clash_relay.routing_shadow import routing_shadow_summary


def _project(repo_root):
    return load_project(
        config_path=repo_root / "config.yaml",
        subscriptions_path=repo_root / "subscriptions.yaml",
        services_path=repo_root / "services.yaml",
        policies_path=repo_root / "policies.yaml",
    )


def test_complex_concurrent_scenarios_have_independent_route_intent(repo_root) -> None:
    project = _project(repo_root)
    model = compile_routing_model(project.acl4ssr)
    assert model is not None
    rows = {row["source_id"]: row for row in model["bindings"]}

    expected = {
        # Domestic web remains direct.
        "china_domain": ("direct", "domestic_web", "全球直连"),
        "china_company_ip": ("direct", "domestic_web", "全球直连"),
        # Explicit generic foreign web uses the browsing inventory.
        "proxy_gfwlist": ("browsing", "foreign_web", "网页浏览"),
        # Video/media services are classified independently from generic web.
        "youtube": ("media", "youtube", "油管视频"),
        "netflix": ("media", "netflix", "奈飞视频"),
        "bilibili": ("media", "bilibili", "哔哩哔哩"),
        "china_media": ("media", "china_media", "国内媒体"),
        "proxy_media": ("media", "foreign_media", "国外媒体"),
        # Download is a first-class general-only automatic scenario.
        "download": ("download", "download", "下载流量"),
        # AI services keep an independent service dimension.
        "openai": ("ai", "openai", "人工智能"),
        "ai": ("ai", "generic_ai", "人工智能"),
        # Unknown traffic remains on the canonical final route.
        "__final__": ("final", None, "漏网之鱼"),
    }
    for source_id, (scenario, service, target) in expected.items():
        row = rows[source_id]
        assert row["scenario"] == scenario
        assert row.get("service") == service
        assert row["target"] == target


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


def test_specific_services_and_download_precede_generic_foreign_web(repo_root) -> None:
    project = _project(repo_root)
    model = compile_routing_model(project.acl4ssr)
    assert model is not None
    rows = {row["source_id"]: row for row in model["bindings"]}
    generic_web_priority = rows["proxy_gfwlist"]["priority"]

    for source_id in (
        "ai",
        "openai",
        "youtube",
        "netflix",
        "bilibili",
        "proxy_media",
        "download",
    ):
        assert rows[source_id]["priority"] < generic_web_priority
    assert rows["china_domain"]["priority"] < rows["download"]["priority"]
    assert rows["china_company_ip"]["priority"] < rows["download"]["priority"]
    assert rows["geoip_cn"]["priority"] < rows["download"]["priority"]


def test_cutover_keeps_foreign_web_classifier_narrow(repo_root) -> None:
    shadow = routing_shadow_summary(_project(repo_root))

    assert shadow["status"] == "cutover"
    assert shadow["foreign_web"]["explicit_rule_sources"] == 1
    assert shadow["foreign_web"]["classifier_widened"] is False
    assert shadow["download"] == {
        "current_mode": "general_auto",
        "cutover_mode": "general_auto",
        "affected_rule_sources": 1,
        "cutover_applied": True,
    }
    assert shadow["media"] == {
        "auto_scheduler_rule_sources": 2,
        "cutover_applied": True,
    }
    assert shadow["ai"]["excluded_regions"] == ["HK"]
    assert shadow["ai"]["current_region_order"] == ["US", "SG", "JP", "TW", "KR", "OTHER"]
    assert shadow["ai"]["cutover_applied"] is True
    assert shadow["ai"]["region_order_would_change"] is False
