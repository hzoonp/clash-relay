from __future__ import annotations

from pathlib import Path

import pytest

import clash_relay.browsing_qualification as browsing_qualification
from clash_relay.browsing_qualification import (
    _latency_summary,
    _probe_node,
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


def test_browsing_qualification_requires_two_of_three_successful_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes = iter(
        [
            (110, "success"),
            (None, "probe_error"),
            (130, "success"),
        ]
    )
    monkeypatch.setattr(
        browsing_qualification,
        "_delay_probe",
        lambda *_args, **_kwargs: next(outcomes),
    )

    passed, delays, counts = _probe_node(
        1,
        "secret",
        "node",
        {"url": "https://example.test", "timeout": 1000, "expected_status": "204"},
        attempts=3,
        required_successes=2,
    )

    assert passed is True
    assert delays == (110, 130)
    assert counts == {"success": 2, "probe_error": 1}


def test_browsing_qualification_rejects_one_of_three_successful_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes = iter(
        [
            (None, "probe_error"),
            (120, "success"),
            (None, "controller_http_504"),
        ]
    )
    monkeypatch.setattr(
        browsing_qualification,
        "_delay_probe",
        lambda *_args, **_kwargs: next(outcomes),
    )

    passed, delays, _counts = _probe_node(
        1,
        "secret",
        "node",
        {"url": "https://example.test", "timeout": 1000, "expected_status": "204"},
        attempts=3,
        required_successes=2,
    )

    assert passed is False
    assert delays == (120,)


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
    assert config["proxy-providers"]["cr_general_any"]["payload"] == [
        {"name": "general-stays"}
    ]
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
