from __future__ import annotations

import urllib.parse
from pathlib import Path

import pytest

import clash_relay.browsing_qualification as browsing_qualification
from clash_relay.browsing_qualification import (
    _group_delay_probe,
    _latency_summary,
    _qualified_from_group_samples,
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


def test_apply_browsing_qualification_only_prunes_browsing_inventory() -> None:
    config = {
        "proxy-providers": {
            "cr_browsing_any": {
                "type": "inline",
                "payload": [{"name": "keep"}, {"name": "drop"}],
            },
            "cr_general_any": {
                "type": "inline",
                "payload": [{"name": "general-stays"}],
            },
            "cr_ai_us": {
                "type": "inline",
                "payload": [{"name": "ai-stays"}],
            },
        }
    }

    report = apply_browsing_qualification(config, {"keep"})

    assert config["proxy-providers"]["cr_browsing_any"]["payload"] == [{"name": "keep"}]
    assert config["proxy-providers"]["cr_general_any"]["payload"] == [{"name": "general-stays"}]
    assert config["proxy-providers"]["cr_ai_us"]["payload"] == [{"name": "ai-stays"}]
    assert report == {
        "tested_nodes": 2,
        "qualified_nodes": 1,
        "failed_nodes": 1,
        "providers": {"cr_browsing_any": {"tested": 2, "qualified": 1}},
    }


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
        apply_browsing_qualification(config, set())


def test_latency_summary_is_aggregate_only() -> None:
    assert _latency_summary([100.0, 110.0, 120.0, 130.0]) == {
        "min": 100.0,
        "p50": 115.0,
        "p95": 130.0,
        "max": 130.0,
    }
    assert _latency_summary([]) == {"min": None, "p50": None, "p95": None, "max": None}
