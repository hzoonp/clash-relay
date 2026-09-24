from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

import pytest

import clash_relay.browsing_qualification as browsing_qualification
from clash_relay.browsing_qualification import (
    _core_rejection_candidate_proxies,
    _group_delay_probe,
    _latency_summary,
    _proxy_identity,
    _prune_rejected_proxy_identities,
    _qualified_from_group_samples,
    _stability_tiers_from_group_samples,
    apply_browsing_qualification,
    load_browsing_probe_spec,
)
from clash_relay.errors import ValidationError


def test_canonical_browsing_probe_is_reused_for_pre_publish_qualification(
    repo_root: Path,
) -> None:
    probe = load_browsing_probe_spec(repo_root / "policies.yaml")
    assert probe == {
        "name": "browsing",
        "url": "https://cp.cloudflare.com/generate_204",
        "expected_status": "204",
        "timeout": 8000,
    }


def test_group_delay_probe_uses_provider_compatible_group_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def fake_controller_json(
        port: int,
        secret: str,
        path: str,
        *,
        timeout: float,
    ) -> dict[str, object]:
        seen.update(port=port, secret=secret, path=path, timeout=timeout)
        return {"node-a": 0, "node-b": 240}

    monkeypatch.setattr(browsing_qualification, "_controller_json", fake_controller_json)
    sample, outcome = _group_delay_probe(
        9090,
        "secret",
        {
            "url": "https://www.gstatic.com/generate_204",
            "timeout": 5000,
            "expected_status": "204",
        },
    )

    path = str(seen["path"])
    parsed = urllib.parse.urlsplit(path)
    query = urllib.parse.parse_qs(parsed.query)
    assert parsed.path == "/group/__CR_BROWSING_QUALIFICATION/delay"
    assert query == {
        "url": ["https://www.gstatic.com/generate_204"],
        "timeout": ["5000"],
        "expected": ["204"],
    }
    assert sample == {"node-a": 0, "node-b": 240}
    assert outcome == "success"


def test_browsing_qualification_requires_two_of_three_group_samples() -> None:
    qualified, medians = _qualified_from_group_samples(
        ("node-a", "node-b"),
        (
            {"node-a": 0, "node-b": 200},
            {"node-b": 210},
            {"node-a": 0},
        ),
        required_successes=2,
    )

    assert qualified == {"node-a", "node-b"}
    assert medians == [0.0, 205.0]


def test_browsing_stability_tiers_keep_two_of_three_as_reserve() -> None:
    qualified, stable, medians = _stability_tiers_from_group_samples(
        ("stable", "reserve", "failed"),
        (
            {"stable": 100, "reserve": 200, "failed": 300},
            {"stable": 110, "reserve": 210},
            {"stable": 120},
        ),
        required_successes=2,
    )

    assert qualified == {"stable", "reserve"}
    assert stable == {"stable"}
    assert medians == [205.0, 110.0]


def test_browsing_qualification_rejects_one_of_three_group_samples() -> None:
    qualified, medians = _qualified_from_group_samples(
        ("node-a", "node-b"),
        (
            {"node-a": 120},
            {},
            {"node-b": 220},
        ),
        required_successes=2,
    )

    assert qualified == set()
    assert medians == []


def test_apply_browsing_qualification_keeps_reserve_manual_but_not_automatic() -> None:
    config = {
        "proxy-providers": {
            "cr_browsing_any": {
                "type": "inline",
                "payload": [
                    {"name": "stable-a"},
                    {"name": "stable-b"},
                    {"name": "stable-c"},
                    {"name": "reserve"},
                    {"name": "drop"},
                ],
            },
            "cr_general_any": {
                "type": "inline",
                "payload": [{"name": "general-stays"}],
            },
            "cr_ai_us": {
                "type": "inline",
                "payload": [{"name": "ai-stays"}],
            },
        },
        "proxy-groups": [
            {
                "name": "Browsing Auto",
                "type": "url-test",
                "use": ["cr_browsing_any"],
                "filter": ".*",
            },
            {
                "name": "Browsing Manual",
                "type": "select",
                "use": ["cr_browsing_any"],
            },
        ],
    }

    report = apply_browsing_qualification(
        config,
        {"stable-a", "stable-b", "stable-c", "reserve"},
        {"stable-a", "stable-b", "stable-c"},
    )

    assert config["proxy-providers"]["cr_browsing_any"]["payload"] == [
        {"name": "stable-a"},
        {"name": "stable-b"},
        {"name": "stable-c"},
        {"name": "reserve"},
    ]
    assert "cr_browsing_auto_any" not in config["proxy-providers"]
    assert config["proxy-groups"][0]["use"] == ["cr_browsing_any"]
    auto_filter = config["proxy-groups"][0]["filter"]
    assert re.fullmatch(auto_filter, "stable-a")
    assert re.fullmatch(auto_filter, "stable-b")
    assert re.fullmatch(auto_filter, "stable-c")
    assert re.fullmatch(auto_filter, "reserve") is None
    assert config["proxy-groups"][1]["use"] == ["cr_browsing_any"]
    assert config["proxy-providers"]["cr_general_any"]["payload"] == [{"name": "general-stays"}]
    assert config["proxy-providers"]["cr_ai_us"]["payload"] == [{"name": "ai-stays"}]
    assert report["tested_nodes"] == 5
    assert report["qualified_nodes"] == 4
    assert report["stable_nodes"] == 3
    assert report["reserve_nodes"] == 1
    assert report["failed_nodes"] == 1
    assert report["automatic_nodes"] == 3
    assert report["automatic_fallback_providers"] == 0
    assert report["automatic_groups"] == 1


def test_apply_browsing_qualification_falls_back_when_stable_tier_is_too_small() -> None:
    config = {
        "proxy-providers": {
            "cr_browsing_any": {
                "type": "inline",
                "payload": [{"name": "stable"}, {"name": "reserve-a"}, {"name": "reserve-b"}],
            }
        },
        "proxy-groups": [
            {
                "name": "Browsing Auto",
                "type": "url-test",
                "use": ["cr_browsing_any"],
                "filter": ".*",
            }
        ],
    }

    report = apply_browsing_qualification(
        config,
        {"stable", "reserve-a", "reserve-b"},
        {"stable"},
    )

    assert set(config["proxy-providers"]) == {"cr_browsing_any"}
    auto_filter = config["proxy-groups"][0]["filter"]
    assert re.fullmatch(auto_filter, "stable")
    assert re.fullmatch(auto_filter, "reserve-a")
    assert re.fullmatch(auto_filter, "reserve-b")
    assert report["automatic_fallback_providers"] == 1
    assert report["automatic_nodes"] == 3


def test_apply_browsing_qualification_quotes_runtime_names_in_exact_filter() -> None:
    names = {"stable[1]", "stable(2)", "stable+3"}
    config = {
        "proxy-providers": {
            "cr_browsing_any": {
                "type": "inline",
                "payload": [{"name": name} for name in sorted(names)],
            }
        },
        "proxy-groups": [
            {
                "name": "Browsing Auto",
                "type": "url-test",
                "use": ["cr_browsing_any"],
                "filter": ".*",
            }
        ],
    }

    apply_browsing_qualification(config, names, names)

    auto_filter = config["proxy-groups"][0]["filter"]
    for name in names:
        assert re.fullmatch(auto_filter, name)
    assert re.fullmatch(auto_filter, "stable1") is None


def test_apply_browsing_qualification_rejects_mixed_provider_auto_group() -> None:
    config = {
        "proxy-providers": {
            "cr_browsing_any": {
                "type": "inline",
                "payload": [
                    {"name": "stable-a"},
                    {"name": "stable-b"},
                    {"name": "stable-c"},
                ],
            },
            "cr_general_any": {
                "type": "inline",
                "payload": [{"name": "general"}],
            },
        },
        "proxy-groups": [
            {
                "name": "Unsafe Mixed Auto",
                "type": "url-test",
                "use": ["cr_browsing_any", "cr_general_any"],
                "filter": ".*",
            }
        ],
    }

    with pytest.raises(ValidationError, match="must not mix browsing and non-browsing providers"):
        apply_browsing_qualification(
            config,
            {"stable-a", "stable-b", "stable-c"},
            {"stable-a", "stable-b", "stable-c"},
        )


def test_apply_browsing_qualification_fails_closed_when_provider_becomes_empty() -> None:
    config = {
        "proxy-providers": {
            "cr_browsing_any": {
                "type": "inline",
                "payload": [{"name": "only-node"}],
            }
        }
    }

    with pytest.raises(ValidationError, match="left provider 'cr_browsing_any' empty"):
        apply_browsing_qualification(config, set(), set())


def test_latency_summary_is_aggregate_only() -> None:
    assert _latency_summary([100.0, 110.0, 120.0, 130.0]) == {
        "min": 100.0,
        "p50": 115.0,
        "p95": 130.0,
        "max": 130.0,
    }
    assert _latency_summary([]) == {"min": None, "p50": None, "p95": None, "max": None}


def test_core_rejection_candidate_proxies_intersect_source_and_type() -> None:
    source5_vless = {
        "name": "[BROWSING:US] sub_5/VLESS #1111111111",
        "type": "vless",
        "server": "one.example",
        "port": 443,
    }
    source5_vmess = {
        "name": "[BROWSING:US] sub_5/VMESS #2222222222",
        "type": "vmess",
        "server": "two.example",
        "port": 443,
    }
    source3_vless = {
        "name": "[BROWSING:US] sub_3/VLESS #3333333333",
        "type": "vless",
        "server": "three.example",
        "port": 443,
    }
    payloads = {
        "cr_browsing_us": (source5_vless, source5_vmess, source3_vless),
    }

    result = _core_rejection_candidate_proxies(
        payloads,
        rejected_sources={"subscription_5"},
        rejected_types={"vless"},
    )

    assert result == (("cr_browsing_us", source5_vless),)


def test_core_quarantine_prunes_same_physical_proxy_across_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad_browsing = {
        "name": "[BROWSING:US] sub_5/Bad #1111111111",
        "type": "vless",
        "server": "bad.example",
        "port": 443,
        "uuid": "fixture-uuid",
    }
    bad_general = {
        **bad_browsing,
        "name": "[GENERAL:ANY] sub_5/Bad #2222222222",
    }
    good_browsing = {
        "name": "[BROWSING:US] sub_2/Good #3333333333",
        "type": "http",
        "server": "good.example",
        "port": 8080,
    }
    good_general = {
        **good_browsing,
        "name": "[GENERAL:ANY] sub_2/Good #4444444444",
    }
    config = {
        "proxy-providers": {
            "cr_browsing_us": {
                "type": "inline",
                "payload": [bad_browsing, good_browsing],
            },
            "cr_general_any": {
                "type": "inline",
                "payload": [bad_general, good_general],
            },
        }
    }
    monkeypatch.setattr(
        browsing_qualification,
        "validate_generated_config",
        lambda _config: None,
    )

    removed = _prune_rejected_proxy_identities(
        config,
        {_proxy_identity(bad_browsing)},
    )

    assert removed == 2
    assert config["proxy-providers"]["cr_browsing_us"]["payload"] == [good_browsing]
    assert config["proxy-providers"]["cr_general_any"]["payload"] == [good_general]


def test_core_quarantine_fails_closed_before_emptying_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    only_proxy = {
        "name": "[BROWSING:US] sub_5/Only #1111111111",
        "type": "vless",
        "server": "bad.example",
        "port": 443,
    }
    config = {
        "proxy-providers": {
            "cr_browsing_us": {
                "type": "inline",
                "payload": [only_proxy],
            }
        }
    }
    monkeypatch.setattr(
        browsing_qualification,
        "validate_generated_config",
        lambda _config: None,
    )

    with pytest.raises(ValidationError, match="would empty a proxy provider"):
        _prune_rejected_proxy_identities(config, {_proxy_identity(only_proxy)})

    assert config["proxy-providers"]["cr_browsing_us"]["payload"] == [only_proxy]


def test_browsing_probe_detaches_production_dns_rule_set_policy() -> None:
    base = {
        "proxy-providers": {
            "cr_browsing_any": {
                "type": "inline",
                "payload": [{"name": "node-a", "type": "http", "server": "a.invalid", "port": 443}],
            }
        },
        "dns": {
            "enable": True,
            "listen": "127.0.0.1:1053",
            "nameserver-policy": {"rule-set:acl4ssr_proxy_lite": ["https://1.1.1.1/dns-query"]},
        },
    }

    probe = browsing_qualification._temporary_probe_config(
        base,
        {"cr_browsing_any": (base["proxy-providers"]["cr_browsing_any"]["payload"][0],)},
        mixed_port=17890,
        controller_port=19090,
        secret="test",
    )

    assert "nameserver-policy" not in probe["dns"]
