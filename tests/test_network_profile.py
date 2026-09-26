from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from clash_relay.builder import build_candidate
from clash_relay.errors import ConfigurationError, GenerationError
from clash_relay.network_profile import (
    NETWORK_PROFILE_CN_THREE_NET,
    NETWORK_PROFILE_DEFAULT,
    apply_network_profile,
    apply_network_profile_urltest,
    resolve_network_profile,
)
from clash_relay.schema import load_and_validate


def _config(profile: str | None = None, *, dns_mode: str = "managed") -> dict:
    dns: dict = {
        "mode": dns_mode,
        "enabled": True,
        "enhanced_mode": "fake-ip",
        "listen": "127.0.0.1:1053",
        "default_nameservers": ["https://1.1.1.1/dns-query"],
        "nameservers": ["https://1.1.1.1/dns-query"],
        "proxy_server_nameservers": ["https://1.1.1.1/dns-query"],
        "direct_nameservers": ["https://1.1.1.1/dns-query"],
        "direct_nameserver_follow_policy": False,
        "nameserver_policy_overrides": {"stun.l.google.com": ["https://dns.alidns.com/dns-query"]},
        "fallback_nameservers": [],
    }
    runtime: dict = {"dns": dns}
    if profile is not None:
        runtime["network_profile"] = profile
    return {"runtime": runtime}


def test_default_profile_leaves_declared_dns_untouched() -> None:
    config = _config()

    assert apply_network_profile(config) == {
        "profile": NETWORK_PROFILE_DEFAULT,
        "status": "not_applicable",
        "dns_overrides": [],
        "urltest_tuning": False,
    }
    assert config["runtime"]["dns"]["proxy_server_nameservers"] == ["https://1.1.1.1/dns-query"]


def test_cn_three_net_overrides_resolver_pools_and_preserves_policy_overrides() -> None:
    config = _config(NETWORK_PROFILE_CN_THREE_NET)

    report = apply_network_profile(config)

    assert report["status"] == "applied"
    assert report["dns_overrides"] == [
        "default_nameservers",
        "direct_nameservers",
        "nameservers",
        "proxy_server_nameservers",
    ]
    dns = config["runtime"]["dns"]
    assert dns["default_nameservers"] == ["223.5.5.5", "119.29.29.29"]
    assert dns["nameservers"] == [
        "https://dns.alidns.com/dns-query",
        "https://doh.pub/dns-query",
    ]
    assert dns["proxy_server_nameservers"] == [
        "system",
        "https://dns.alidns.com/dns-query",
        "https://doh.pub/dns-query",
    ]
    assert dns["direct_nameservers"] == [
        "system",
        "https://dns.alidns.com/dns-query",
        "https://doh.pub/dns-query",
    ]
    # The exact STUN override and DIRECT policy bypass are preserved verbatim:
    # no wildcard keys and no resolver-transport recursion into routing.
    assert dns["nameserver_policy_overrides"] == {
        "stun.l.google.com": ["https://dns.alidns.com/dns-query"]
    }
    assert dns["direct_nameserver_follow_policy"] is False


def test_cn_three_net_requires_enabled_managed_dns() -> None:
    config = _config(NETWORK_PROFILE_CN_THREE_NET, dns_mode="client")

    with pytest.raises(ConfigurationError, match="requires enabled managed DNS"):
        apply_network_profile(config)


def test_unknown_profile_fails_closed() -> None:
    with pytest.raises(ConfigurationError, match=r"unsupported runtime\.network_profile"):
        resolve_network_profile(_config("cn_telecom_only"))


def test_profile_dns_override_does_not_share_module_state() -> None:
    first = _config(NETWORK_PROFILE_CN_THREE_NET)
    second = _config(NETWORK_PROFILE_CN_THREE_NET)
    apply_network_profile(first)
    first["runtime"]["dns"]["nameservers"].append("https://evil.example/dns-query")

    apply_network_profile(second)

    assert second["runtime"]["dns"]["nameservers"] == [
        "https://dns.alidns.com/dns-query",
        "https://doh.pub/dns-query",
    ]


def _urltest_output() -> dict:
    return {
        "proxy-groups": [
            {
                "name": "香港节点",
                "type": "url-test",
                "url": "https://cp.cloudflare.com/generate_204",
                "interval": 300,
                "timeout": 8000,
                "tolerance": 50,
            },
            {
                "name": "美国节点",
                "type": "url-test",
                "url": "https://cp.cloudflare.com/generate_204",
                "interval": 300,
                "timeout": 8000,
                "tolerance": 150,
            },
            {
                "name": "下载自动",
                "type": "url-test",
                "url": "https://cp.cloudflare.com/generate_204",
                "interval": 600,
                "timeout": 5000,
                "tolerance": 300,
            },
        ],
        "proxy-providers": {
            "cr_general_hk": {
                "type": "inline",
                "health-check": {
                    "enable": True,
                    "url": "https://cp.cloudflare.com/generate_204",
                },
            }
        },
    }


def _group_specs() -> list[dict]:
    return [
        {"id": "policy_hk", "display_name": "香港节点"},
        {"id": "policy_us", "display_name": "美国节点"},
        {"id": "policy_download_auto", "display_name": "下载自动"},
    ]


def _policies() -> dict:
    return {
        "probes": {
            "browsing": {
                "url": "https://cp.cloudflare.com/generate_204",
                "interval": 180,
                "timeout": 8000,
            }
        },
        "scheduler": {"browsing": {"region_switch_interval": 300}},
    }


def test_urltest_tuning_not_applicable_for_default_profile() -> None:
    output = _urltest_output()

    report = apply_network_profile_urltest(
        output,
        config=_config(),
        policies=_policies(),
        group_specs=_group_specs(),
    )

    assert report == {"profile": NETWORK_PROFILE_DEFAULT, "status": "not_applicable"}
    assert output["proxy-groups"][0]["tolerance"] == 50


def test_cn_three_net_widens_regional_tolerance_without_carrier_hardcoding() -> None:
    output = _urltest_output()

    report = apply_network_profile_urltest(
        output,
        config=_config(NETWORK_PROFILE_CN_THREE_NET),
        policies=_policies(),
        group_specs=_group_specs(),
    )

    assert report["status"] == "applied"
    assert report["tolerance_overrides"] == {"香港节点": 120, "美国节点": 150}
    groups = {group["name"]: group for group in output["proxy-groups"]}
    assert groups["香港节点"]["tolerance"] == 120
    assert groups["美国节点"]["tolerance"] == 150
    assert report["regional"]["interval"] == 300
    assert report["regional"]["timeout"] == 8000
    assert report["regional"]["max_failed_times"] == {
        "browsing": 1,
        "regional_and_other": 2,
    }


def test_cn_three_net_rejects_regional_group_that_lost_the_regional_contract() -> None:
    output = _urltest_output()
    output["proxy-groups"][0]["timeout"] = 5000

    with pytest.raises(GenerationError, match="8000ms"):
        apply_network_profile_urltest(
            output,
            config=_config(NETWORK_PROFILE_CN_THREE_NET),
            policies=_policies(),
            group_specs=_group_specs(),
        )


def test_cn_three_net_rejects_non_canonical_automatic_probe() -> None:
    output = _urltest_output()
    output["proxy-groups"].append(
        {
            "name": "自动选择",
            "type": "url-test",
            "url": "http://cp.cloudflare.com/generate_204",
        }
    )

    with pytest.raises(GenerationError, match="canonical HTTPS Cloudflare"):
        apply_network_profile_urltest(
            output,
            config=_config(NETWORK_PROFILE_CN_THREE_NET),
            policies=_policies(),
            group_specs=_group_specs(),
        )


def test_cn_three_net_rejects_non_canonical_provider_health_check() -> None:
    output = _urltest_output()
    output["proxy-providers"]["cr_general_hk"]["health-check"]["url"] = (
        "https://www.gstatic.com/generate_204"
    )

    with pytest.raises(GenerationError, match="provider health check"):
        apply_network_profile_urltest(
            output,
            config=_config(NETWORK_PROFILE_CN_THREE_NET),
            policies=_policies(),
            group_specs=_group_specs(),
        )


def test_cn_three_net_requires_canonical_browsing_probe_contract() -> None:
    output = _urltest_output()
    policies = _policies()
    policies["probes"]["browsing"]["interval"] = 120

    with pytest.raises(GenerationError, match="180s browsing interval"):
        apply_network_profile_urltest(
            output,
            config=_config(NETWORK_PROFILE_CN_THREE_NET),
            policies=policies,
            group_specs=_group_specs(),
        )


def test_cn_three_net_requires_low_frequency_high_tolerance_download_group() -> None:
    output = _urltest_output()
    output["proxy-groups"][2]["interval"] = 120

    with pytest.raises(GenerationError, match="low-frequency"):
        apply_network_profile_urltest(
            output,
            config=_config(NETWORK_PROFILE_CN_THREE_NET),
            policies=_policies(),
            group_specs=_group_specs(),
        )


_REGIONAL_GROUPS = ("香港节点", "台湾节点", "新加坡节点", "日本节点", "韩国节点", "美国节点")


def _canonical_project(
    repo_root: Path, tmp_path: Path, *, network_profile: str | None
) -> dict[str, Path]:
    root = tmp_path / "canonical-profile"
    root.mkdir()
    for name in ("subscriptions.yaml", "policies.yaml"):
        (root / name).write_text((repo_root / name).read_text(encoding="utf-8"), encoding="utf-8")
    shutil.copytree(repo_root / "policies", root / "policies")
    shutil.copytree(repo_root / "rules", root / "rules")
    config = yaml.safe_load((repo_root / "config.yaml").read_text(encoding="utf-8"))
    config["generation"]["allow_file_subscription_urls"] = True
    if network_profile is None:
        config["runtime"].pop("network_profile", None)
    else:
        config["runtime"]["network_profile"] = network_profile
    (root / "config.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return {
        "config_path": root / "config.yaml",
        "subscriptions_path": root / "subscriptions.yaml",
        "policies_path": root / "policies.yaml",
    }


def _canonical_fetcher(url: str, **_kwargs) -> str:
    source = int(url.rsplit("/", 1)[1])
    regions = {
        1: ["Hong Kong", "Taiwan", "Singapore", "Japan", "Korea", "US"],
        2: ["Hong Kong", "Taiwan"],
        3: ["Singapore", "Japan"],
        4: ["Korea"],
        5: ["US"],
    }[source]
    proxies = [
        {
            "name": f"{region} Node S{source}",
            "type": "http",
            "server": f"{region.lower().replace(' ', '-')}-s{source}.fixture.invalid",
            "port": 20000 + source,
        }
        for region in regions
    ]
    return yaml.safe_dump({"proxies": proxies}, allow_unicode=True, sort_keys=False)


def _canonical_rule_fetcher(_url: str, **_kwargs) -> str:
    return "DOMAIN-SUFFIX,fixture.invalid\n"


def _build_canonical(repo_root: Path, tmp_path: Path, *, network_profile: str | None):
    paths = _canonical_project(repo_root, tmp_path, network_profile=network_profile)
    urls = {
        f"SUBSCRIPTION_{index}_URL": f"https://fixture.invalid/sub/{index}" for index in range(1, 6)
    }
    return build_candidate(
        **paths,
        env=urls,
        fetcher=_canonical_fetcher,
        rule_fetcher=_canonical_rule_fetcher,
    )


def test_cn_three_net_end_to_end_build_applies_profile(repo_root, tmp_path) -> None:
    result = _build_canonical(repo_root, tmp_path, network_profile=NETWORK_PROFILE_CN_THREE_NET)
    candidate = result.config
    dns = candidate["dns"]

    assert dns["proxy-server-nameserver"] == [
        "system",
        "https://dns.alidns.com/dns-query",
        "https://doh.pub/dns-query",
    ]
    assert dns["default-nameserver"] == ["223.5.5.5", "119.29.29.29"]
    assert dns["nameserver"] == [
        "https://dns.alidns.com/dns-query",
        "https://doh.pub/dns-query",
    ]
    assert dns["direct-nameserver"] == [
        "system",
        "https://dns.alidns.com/dns-query",
        "https://doh.pub/dns-query",
    ]
    # The exact STUN override and ACL4SSR rule-set routes survive the profile.
    assert dns["nameserver-policy"]["stun.l.google.com"] == [
        "https://dns.alidns.com/dns-query",
        "https://doh.pub/dns-query",
    ]
    assert any(str(key).startswith("rule-set:") for key in dns["nameserver-policy"])
    assert dns["direct-nameserver-follow-policy"] is False

    groups = {group["name"]: group for group in candidate["proxy-groups"]}
    for name in _REGIONAL_GROUPS:
        assert groups[name]["interval"] == 300, name
        assert groups[name]["timeout"] == 8000, name
    for name in _REGIONAL_GROUPS:
        expected = 150 if name == "美国节点" else 120
        assert groups[name]["tolerance"] == expected, name

    # Browsing tiers keep the 180s/8000ms contract; the regional switch layer
    # keeps the 300s interval. max-failed-times is applied by the qualification
    # acceleration stage (browsing 1, regional and other non-AI 2).
    browsing_tiers = [
        group
        for group in candidate["proxy-groups"]
        if group.get("type") == "url-test" and str(group["name"]).startswith("__CR_BROWSING_")
    ]
    assert browsing_tiers
    for group in browsing_tiers:
        assert group["interval"] == 180
        assert group["timeout"] == 8000
    assert groups["网页自动"]["interval"] == 300
    assert groups["网页自动"]["type"] == "url-test"
    assert groups["网页自动"]["tolerance"] == 150

    assert result.report["network_profile"]["profile"] == NETWORK_PROFILE_CN_THREE_NET
    urltest_report = result.report["network_profile_urltest"]
    assert urltest_report["status"] == "applied"
    assert urltest_report["tolerance_overrides"] == {
        "香港节点": 120,
        "台湾节点": 120,
        "新加坡节点": 120,
        "日本节点": 120,
        "韩国节点": 120,
        "美国节点": 150,
    }
    assert urltest_report["automatic_groups_checked"] > 0


def test_default_profile_end_to_end_build_keeps_canonical_resolver_pools(
    repo_root, tmp_path
) -> None:
    result = _build_canonical(repo_root, tmp_path, network_profile=None)
    candidate = result.config

    assert candidate["dns"]["proxy-server-nameserver"] == [
        "https://1.1.1.1/dns-query",
        "https://8.8.8.8/dns-query",
        "https://223.5.5.5/dns-query",
    ]
    groups = {group["name"]: group for group in candidate["proxy-groups"]}
    assert groups["香港节点"]["tolerance"] == 50
    assert groups["美国节点"]["tolerance"] == 150
    assert "network_profile_urltest" not in result.report
    assert result.report["network_profile"]["status"] == "not_applicable"


def test_canonical_public_configs_validate_against_schema(repo_root: Path) -> None:
    load_and_validate(repo_root / "config.yaml", "config.schema.json")
    load_and_validate(repo_root / "config.example.yaml", "config.schema.json")


@pytest.mark.parametrize("all_reserve", [False, True])
def test_endpoint_caps_preserve_canonical_production_audits(repo_root, tmp_path, all_reserve):
    import copy

    from clash_relay.config_loader import load_project
    from clash_relay.dns_runtime_audit import audit_dns_runtime_dependencies
    from clash_relay.production_pipeline import audit_candidate
    from clash_relay.proxy_endpoint_qualification import cap_endpoint_reserve_pools
    from clash_relay.validator import validate_generated_config

    result = _build_canonical(repo_root, tmp_path, network_profile=NETWORK_PROFILE_CN_THREE_NET)
    root = tmp_path / "canonical-profile"
    project = load_project(
        config_path=root / "config.yaml",
        subscriptions_path=root / "subscriptions.yaml",
        policies_path=root / "policies.yaml",
    )
    candidate = result.config
    names = [
        proxy["name"]
        for provider in candidate["proxy-providers"].values()
        for proxy in provider["payload"]
    ]
    reserves = set(names if all_reserve else names[::2])
    assert reserves
    assert cap_endpoint_reserve_pools(candidate, reserves) > 0
    validate_generated_config(candidate)
    audit_candidate(project, candidate, build_report=result.report)
    assert audit_dns_runtime_dependencies(candidate)["status"] == "passed"
    first = copy.deepcopy(candidate)
    assert cap_endpoint_reserve_pools(candidate, reserves) == 0
    assert candidate == first
