from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

import pytest

import clash_relay.browsing_qualification as browsing_qualification
from clash_relay.browsing_qualification import (
    _group_delay_probe,
    _latency_summary,
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
        "url": "https://www.gstatic.com/generate_204",
        "expected_status": "204",
        "timeout": 3000,
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
            "timeout": 3000,
            "expected_status": "204",
        },
    )

    path = str(seen["path"])
    parsed = urllib.parse.urlsplit(path)
    query = urllib.parse.parse_qs(parsed.query)
    assert parsed.path == "/group/__CR_BROWSING_QUALIFICATION/delay"
    assert query == {
        "url": ["https://www.gstatic.com/generate_204"],
        "timeout": ["3000"],
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