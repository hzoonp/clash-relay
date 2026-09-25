from __future__ import annotations

import json

import pytest

from clash_relay.carrier_qualification import (
    CarrierProbeResult,
    aggregate_carrier_results,
    run_carrier_qualification,
)
from clash_relay.errors import ValidationError


def test_carrier_qualification_is_not_configured_without_probes() -> None:
    report = run_carrier_qualification()

    assert report["status"] == "not_configured"
    assert report["carriers"] == {}
    assert "reserved extension point" in report["note"]
    assert "global endpoint preflight" in report["note"]


def test_carrier_results_reduce_to_aggregate_only_output() -> None:
    report = run_carrier_qualification(
        [
            CarrierProbeResult(carrier="telecom", tested=40, reachable=38, median_latency_ms=52.4),
            CarrierProbeResult(carrier="unicom", tested=40, reachable=35, median_latency_ms=61.0),
            CarrierProbeResult(carrier="mobile", tested=40, reachable=39, median_latency_ms=48.2),
        ]
    )

    assert report["status"] == "passed"
    assert report["carriers"] == {
        "mobile": {"carrier": "mobile", "tested": 40, "reachable": 39, "median_latency_ms": 48.2},
        "telecom": {"carrier": "telecom", "tested": 40, "reachable": 38, "median_latency_ms": 52.4},
        "unicom": {"carrier": "unicom", "tested": 40, "reachable": 35, "median_latency_ms": 61.0},
    }
    assert report["aggregate"] == {
        "carriers_reported": 3,
        "tested": 120,
        "reachable": 112,
        "reachable_ratio": 0.9333,
        "median_latency_ms": 52.4,
    }
    # The boundary never carries endpoint identities, only aggregates.
    serialized = json.dumps(report)
    assert "server" not in serialized
    assert "endpoint" not in serialized


def test_carrier_results_reject_invalid_probe_rows() -> None:
    with pytest.raises(ValidationError, match="known carrier"):
        CarrierProbeResult(carrier="china_telecom", tested=10, reachable=5, median_latency_ms=50)
    with pytest.raises(ValidationError, match="reachable"):
        CarrierProbeResult(carrier="telecom", tested=10, reachable=11, median_latency_ms=50)
    with pytest.raises(ValidationError, match="positive sample count"):
        CarrierProbeResult(carrier="telecom", tested=0, reachable=0, median_latency_ms=50)
    with pytest.raises(ValidationError, match="numeric median latency"):
        CarrierProbeResult(carrier="telecom", tested=10, reachable=5, median_latency_ms="50")


def test_carrier_aggregate_rejects_raw_mapping_input() -> None:
    with pytest.raises(ValidationError, match="CarrierProbeResult rows"):
        run_carrier_qualification({"telecom": {"tested": 10}})


def test_aggregate_helper_and_pipeline_entry_point_agree() -> None:
    rows = [
        CarrierProbeResult(carrier="telecom", tested=10, reachable=10, median_latency_ms=40),
        CarrierProbeResult(carrier="mobile", tested=10, reachable=5, median_latency_ms=60),
    ]

    assert aggregate_carrier_results(rows) == run_carrier_qualification(rows)
