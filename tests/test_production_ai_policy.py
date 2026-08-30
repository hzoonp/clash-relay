from clash_relay.config_loader import load_project


AI_COUNTRY_GROUPS = [
    "AI · 新加坡",
    "AI · 日本",
    "AI · 美国",
    "AI · 香港",
    "AI · 台湾",
    "AI · 韩国",
    "AI · 其他地区",
]


def _project(repo_root):
    return load_project(
        config_path=repo_root / "config.yaml",
        subscriptions_path=repo_root / "subscriptions.yaml",
        services_path=repo_root / "services.yaml",
        policies_path=repo_root / "policies.yaml",
    )


def test_production_ai_candidates_are_country_classified_and_live_gated(repo_root) -> None:
    project = _project(repo_root)
    policies = project.policies
    subscriptions = project.subscriptions_document
    acl = project.acl4ssr
    assert acl is not None

    assert set(policies["country_classification"]["aliases"]) >= {"HK", "TW", "SG", "JP", "US", "KR"}
    for subscription in subscriptions["subscriptions"]:
        assert "ai" in subscription["allowed_uses"]
        assert "*" in subscription["allowed_countries"]

    pools = {item["display_name"]: item for item in policies["pools"]}
    for group_name in AI_COUNTRY_GROUPS:
        pool = pools[group_name]
        assert pool["source_use"] == "ai"
        assert pool["on_empty"] == "reject"
        assert pool["probe"] == "connectivity"

    for probe_name in ("ai_openai", "ai_claude", "ai_gemini"):
        probe = policies["probes"][probe_name]
        assert probe["url"].startswith("https://")
        assert probe["expected_status"] == "200-399"

    ai_group = next(item for item in acl["groups"] if item["display_name"] == "人工智能")
    members = [item.get("group", item.get("builtin")) for item in ai_group["members"]]
    assert members == [*AI_COUNTRY_GROUPS, "DIRECT"]


def test_production_project_with_ai_country_pools_is_schema_valid(repo_root) -> None:
    project = _project(repo_root)
    assert project.config["modules"]["general"] is True
    assert {pool["display_name"] for pool in project.policies["pools"]} >= set(AI_COUNTRY_GROUPS)
