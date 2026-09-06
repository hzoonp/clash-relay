from __future__ import annotations

from pathlib import Path

import pytest

from clash_relay.browsing_application import (
    _apply_history_counts,
    _cohort_latency,
    _history_inputs,
    _regional_history_summary,
    _runtime_names_by_region,
)
from clash_relay.errors import ValidationError


def test_history_inputs_require_complete_path_triple(tmp_path: Path) -> None:
    with pytest.raises(
        ValidationError, match="requires history, history_key, and next_history"
    ):
        _history_inputs(
            history=tmp_path / "history.json",
            history_key=None,
            next_history=tmp_path / "next.json",
        )

    assert _history_inputs(history=None, history_key=None, next_history=None) is None


def test_history_inputs_empty_key_disables_history(tmp_path: Path) -> None:
    key = tmp_path / "history.key"
    key.write_text("\n", encoding="ascii")

    assert (
        _history_inputs(
            history=tmp_path / "history.json",
            history_key=key,
            next_history=tmp_path / "next.json",
        )
        is None
    )


def test_history_inputs_reject_invalid_private_key(tmp_path: Path) -> None:
    key = tmp_path / "history.key"
    key.write_text("not-hex\n", encoding="ascii")

    with pytest.raises(ValidationError, match="fingerprint key is invalid"):
        _history_inputs(
            history=tmp_path / "history.json",
            history_key=key,
            next_history=tmp_path / "next.json",
        )


def test_history_inputs_missing_state_degrades_to_empty_history(tmp_path: Path) -> None:
    key = tmp_path / "history.key"
    key.write_text("00" * 32 + "\n", encoding="ascii")

    result = _history_inputs(
        history=tmp_path / "missing-history.json",
        history_key=key,
        next_history=tmp_path / "next.json",
    )

    assert result is not None
    document, fingerprint_key, status = result
    assert status == "missing"
    assert document == {
        "version": 3,
        "nodes": {},
        "cohort": {"runs": 0, "latency_ema_ms": None, "last_seen_epoch": 0},
    }
    assert fingerprint_key == bytes(32)


def test_cohort_latency_accepts_only_non_negative_numeric_p50() -> None:
    assert _cohort_latency({}) is None
    assert _cohort_latency({"qualified_latency_ms": []}) is None
    assert _cohort_latency({"qualified_latency_ms": {"p50": -1}}) is None
    assert _cohort_latency({"qualified_latency_ms": {"p50": "120"}}) is None
    assert _cohort_latency({"qualified_latency_ms": {"p50": 120}}) == 120.0
    assert _cohort_latency({"qualified_latency_ms": {"p50": 120.5}}) == 120.5


def test_runtime_names_by_region_filters_non_browsing_and_invalid_names() -> None:
    candidate = {
        "proxy-providers": {
            "cr_browsing_us": {
                "payload": [
                    {"name": "us-a"},
                    {"name": "us-b"},
                    {"name": 123},
                    "invalid",
                ]
            },
            "cr_browsing_jp": {"payload": [{"name": "jp-a"}]},
            "cr_general_any": {"payload": [{"name": "general"}]},
        }
    }

    assert _runtime_names_by_region(candidate) == {
        "JP": {"jp-a"},
        "US": {"us-a", "us-b"},
    }


def test_runtime_names_by_region_fails_closed_on_invalid_inventory() -> None:
    with pytest.raises(ValidationError, match="requires proxy-providers"):
        _runtime_names_by_region({})

    with pytest.raises(ValidationError, match="invalid provider"):
        _runtime_names_by_region(
            {"proxy-providers": {"cr_browsing_us": {"payload": "not-a-list"}}}
        )

    with pytest.raises(ValidationError, match="no regional inventory"):
        _runtime_names_by_region(
            {"proxy-providers": {"cr_general_any": {"payload": [{"name": "general"}]}}}
        )


def test_regional_history_summary_is_aggregate_only() -> None:
    summary = _regional_history_summary(
        {"US": {"a", "b"}, "JP": {"c"}},
        qualified={"a", "c"},
        stable={"a", "c"},
        preferred={"a"},
    )

    assert summary == {
        "JP": {
            "tested": 1,
            "qualified": 1,
            "stable": 1,
            "preferred_stable": 0,
            "historically_demoted": 1,
        },
        "US": {
            "tested": 2,
            "qualified": 1,
            "stable": 1,
            "preferred_stable": 1,
            "historically_demoted": 0,
        },
    }


def test_apply_history_counts_uses_preferred_floor_and_reserve_fallback() -> None:
    report: dict[str, object] = {"regions": {"US": {}, "JP": {}}}
    names_by_region = {
        "US": {"a", "b", "c", "d"},
        "JP": {"j1"},
    }

    _apply_history_counts(
        report,
        names_by_region=names_by_region,
        qualified={"a", "b", "c", "d", "j1"},
        stable={"a", "b", "c"},
        preferred={"a", "b", "c"},
    )

    assert report["regions"] == {
        "US": {"stable_automatic": 3, "reserve_automatic": 1},
        "JP": {"stable_automatic": 1, "reserve_automatic": 1},
    }
    assert report["stable_automatic_nodes"] == 4
    assert report["reserve_automatic_nodes"] == 2


def test_apply_history_counts_ignores_missing_regions_mapping() -> None:
    report: dict[str, object] = {}

    _apply_history_counts(
        report,
        names_by_region={"US": {"a"}},
        qualified={"a"},
        stable={"a"},
        preferred={"a"},
    )

    assert report == {}
