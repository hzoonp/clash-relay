from __future__ import annotations

import json

import pytest

from clash_relay.carrier_qualification import (
    CarrierProbeResult,
    aggregate_carrier_results,
    parse_carrier_aggregate_payload,
    parse_carrier_json_text,
    run_carrier_qualification,
    safe_carrier_report,
)
from clash_relay.errors import ValidationError


def test_carrier_qualification_is_not_configured_without_probes() -> None:
    report = run_carrier_qualification()

    assert report["status"] == "not_configured"
    assert report["carriers"] == {}
    assert report["authority"] == "external_self_hosted_advisory"
    assert "external self-hosted evidence" in report["note"]
    assert "global endpoint preflight" in report["note"]


def test_carrier_results_reduce_to_aggregate_only_output() -> None:
    report = run_carrier_qualification(
        [
            CarrierProbeResult(carrier="telecom", tested=40, reachable=38, median_latency_ms=52.4),
            CarrierProbeResult(carrier="unicom", tested=40, reachable=35, median_latency_ms=61.0),
            CarrierProbeResult(carrier="mobile", tested=40, reachable=39, median_latency_ms=48.2),
        ]
    )

    assert report["status"] == "full"
    assert report["freshness"]["status"] == "current"
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
    with pytest.raises(ValidationError, match="unknown fields"):
        run_carrier_qualification({"telecom": {"tested": 10}})


def test_carrier_payload_parses_self_hosted_aggregates() -> None:
    payload = {
        "schema_version": 1,
        "collected_at_epoch": 1_000,
        "carriers": {
            "telecom": {"tested": 40, "reachable": 38, "median_latency_ms": 52.4},
            "mobile": {"tested": 40, "reachable": 39, "median_latency_ms": 48.2},
        },
    }

    report = run_carrier_qualification(payload, now_epoch=1_000 + 30)

    assert report["status"] == "partial"
    assert report["freshness"]["status"] == "current"
    assert set(report["carriers"]) == {"mobile", "telecom"}
    assert report["aggregate"]["carriers_reported"] == 2
    assert report["aggregate"]["tested"] == 80
    assert report["aggregate"]["reachable"] == 77


@pytest.mark.parametrize(
    "payload",
    [
        {
            "schema_version": 2,
            "carriers": {"telecom": {"tested": 1, "reachable": 1, "median_latency_ms": 1}},
        },
        {"carriers": {"telecom": {"tested": 1, "reachable": 1, "median_latency_ms": 1}}},
        {"schema_version": 1},
        {
            "schema_version": 1,
            "carriers": {"china_telecom": {"tested": 1, "reachable": 1, "median_latency_ms": 1}},
        },
        {
            "schema_version": 1,
            "carriers": {
                "telecom": {
                    "tested": 1,
                    "reachable": 1,
                    "median_latency_ms": 1,
                    "endpoints": ["1.2.3.4"],
                }
            },
        },
        {
            "schema_version": 1,
            "carriers": {"telecom": {"tested": 0, "reachable": 0, "median_latency_ms": 1}},
        },
        {
            "schema_version": 1,
            "carriers": {"telecom": {"tested": 10, "reachable": 11, "median_latency_ms": 1}},
        },
        {"schema_version": 1, "carriers": {"telecom": {"tested": 10, "reachable": 5}}},
        {"schema_version": 1, "carriers": {}},
    ],
)
def test_carrier_payload_rejects_invalid_or_identity_bearing_input(payload) -> None:
    with pytest.raises(ValidationError):
        run_carrier_qualification(payload)


def test_carrier_payload_never_echoes_input_identities() -> None:
    payload = {
        "schema_version": 1,
        "collected_at_epoch": 5_000,
        "carriers": {"unicom": {"tested": 20, "reachable": 20, "median_latency_ms": 40.0}},
    }

    report = run_carrier_qualification(payload, now_epoch=5_000)
    serialized = json.dumps(report)

    assert report["status"] == "partial"
    assert "endpoints" not in serialized
    assert "probe_server" not in serialized
    # The only "samples" reference is the declared policy constant name; no
    # raw sample payload is echoed.
    assert "minimum_samples_per_carrier" in serialized
    assert '"samples"' not in serialized


def test_parse_carrier_aggregate_payload_rejects_non_mapping() -> None:
    with pytest.raises(ValidationError, match="must be an object"):
        parse_carrier_aggregate_payload(["telecom"])  # type: ignore[arg-type]


def test_aggregate_helper_and_pipeline_entry_point_agree() -> None:
    rows = [
        CarrierProbeResult(carrier="telecom", tested=10, reachable=10, median_latency_ms=40),
        CarrierProbeResult(carrier="mobile", tested=10, reachable=5, median_latency_ms=60),
    ]

    assert aggregate_carrier_results(rows) == run_carrier_qualification(rows)


def test_carrier_payload_freshness_current_and_stale() -> None:
    payload = {
        "schema_version": 1,
        "collected_at_epoch": 1_000,
        "carriers": {"mobile": {"tested": 20, "reachable": 20, "median_latency_ms": 40.0}},
    }

    current = run_carrier_qualification(payload, now_epoch=1_000 + 60)
    assert current["status"] == "partial"
    assert current["freshness"] == {
        "collected_at_epoch": 1_000,
        "age_seconds": 60,
        "max_age_seconds": 6 * 3600,
        "status": "current",
    }

    stale = run_carrier_qualification(payload, now_epoch=1_000 + 7 * 3600)
    assert stale["status"] == "stale"
    assert stale["freshness"]["status"] == "stale"
    assert stale["aggregate"]["tested"] == 20


def test_carrier_payload_timestamp_fails_closed() -> None:
    future = {
        "schema_version": 1,
        "collected_at_epoch": 10_000,
        "carriers": {"telecom": {"tested": 10, "reachable": 5, "median_latency_ms": 1}},
    }
    with pytest.raises(ValidationError, match="future"):
        run_carrier_qualification(future, now_epoch=9_000)

    malformed = {
        "schema_version": 1,
        "collected_at_epoch": "yesterday",
        "carriers": {"telecom": {"tested": 10, "reachable": 5, "median_latency_ms": 1}},
    }
    with pytest.raises(ValidationError, match="integer epoch"):
        run_carrier_qualification(malformed)


def test_duplicate_carrier_rows_fail_before_totals_are_counted() -> None:
    rows = [
        CarrierProbeResult(carrier="telecom", tested=10, reachable=9, median_latency_ms=40),
        CarrierProbeResult(carrier="telecom", tested=20, reachable=20, median_latency_ms=50),
    ]
    with pytest.raises(ValidationError, match="repeats a carrier"):
        run_carrier_qualification(rows)
    with pytest.raises(ValidationError, match="repeats a carrier"):
        aggregate_carrier_results(rows)


def test_duplicate_carrier_keys_in_json_fail_before_mapping_overwrite() -> None:
    text = (
        '{"schema_version":1,"collected_at_epoch":1000,"carriers":{'
        '"telecom":{"tested":1,"reachable":1,"median_latency_ms":40},'
        '"telecom":{"tested":2,"reachable":2,"median_latency_ms":50}}}'
    )
    with pytest.raises(ValidationError, match="repeats a field"):
        parse_carrier_json_text(text)


def test_any_future_timestamp_is_rejected() -> None:
    payload = {
        "schema_version": 1,
        "collected_at_epoch": 1_001,
        "carriers": {"telecom": {"tested": 1, "reachable": 1, "median_latency_ms": 4}},
    }
    with pytest.raises(ValidationError, match="future"):
        run_carrier_qualification(payload, now_epoch=1_000)


def test_safe_carrier_projection_is_aggregate_only_and_deterministic() -> None:
    payload = {
        "schema_version": 1,
        "collected_at_epoch": 1_000,
        "carriers": {
            "telecom": {"tested": 2, "reachable": 1, "median_latency_ms": 40},
            "unicom": {"tested": 2, "reachable": 2, "median_latency_ms": 50},
            "mobile": {"tested": 2, "reachable": 2, "median_latency_ms": 60},
        },
    }
    report = run_carrier_qualification(payload, now_epoch=1_000)
    report["server"] = "secret.example.invalid"
    report["carriers"]["telecom"]["raw_samples"] = ["1.2.3.4"]
    safe = safe_carrier_report(report)
    assert safe["status"] == "full"
    assert safe["authority"] == "external_self_hosted_advisory"
    assert safe["aggregate"] == {
        "carriers_reported": 3,
        "tested": 6,
        "reachable": 5,
        "reachable_ratio": 0.8333,
    }
    assert "median_latency_ms" not in safe["aggregate"]
    assert json.dumps(safe, sort_keys=True) == json.dumps(
        safe_carrier_report(report), sort_keys=True
    )
    assert "secret.example.invalid" not in json.dumps(safe)
    assert "1.2.3.4" not in json.dumps(safe)


def test_single_carrier_is_partial_not_full() -> None:
    report = run_carrier_qualification(
        {
            "schema_version": 1,
            "collected_at_epoch": 1_000,
            "carriers": {"telecom": {"tested": 30, "reachable": 29, "median_latency_ms": 44.0}},
        },
        now_epoch=1_000 + 30,
    )

    assert report["status"] == "partial"
    assert report["freshness"]["status"] == "current"
    assert set(report["carriers"]) == {"telecom"}


def test_two_carriers_are_partial_all_three_is_full() -> None:
    base = {
        "schema_version": 1,
        "collected_at_epoch": 1_000,
        "carriers": {
            "telecom": {"tested": 10, "reachable": 9, "median_latency_ms": 40.0},
            "unicom": {"tested": 10, "reachable": 9, "median_latency_ms": 50.0},
        },
    }
    two = run_carrier_qualification(base, now_epoch=1_000 + 30)
    assert two["status"] == "partial"

    full_payload = dict(base)
    full_payload["carriers"] = {
        **base["carriers"],
        "mobile": {"tested": 10, "reachable": 10, "median_latency_ms": 45.0},
    }
    full = run_carrier_qualification(full_payload, now_epoch=1_000 + 30)
    assert full["status"] == "full"
    assert full["aggregate"]["carriers_reported"] == 3


def test_aggregate_has_no_cross_carrier_median() -> None:
    """Per-carrier medians stay per carrier; the aggregate never invents a
    statistically undefined cross-carrier median."""

    report = run_carrier_qualification(
        [
            CarrierProbeResult(carrier="telecom", tested=10, reachable=10, median_latency_ms=40),
            CarrierProbeResult(carrier="unicom", tested=10, reachable=10, median_latency_ms=80),
        ]
    )

    assert "median_latency_ms" not in report["aggregate"]
    assert report["carriers"]["telecom"]["median_latency_ms"] == 40.0
    assert report["carriers"]["unicom"]["median_latency_ms"] == 80.0


def test_duplicate_carrier_rows_fail_closed_in_sequence() -> None:
    with pytest.raises(ValidationError, match="repeats a carrier"):
        run_carrier_qualification(
            [
                CarrierProbeResult(carrier="telecom", tested=10, reachable=9, median_latency_ms=40),
                CarrierProbeResult(carrier="telecom", tested=10, reachable=8, median_latency_ms=41),
            ]
        )


def test_authority_marker_is_external_self_hosted_advisory() -> None:
    report = run_carrier_qualification()
    assert report["authority"] == "external_self_hosted_advisory"

    populated = run_carrier_qualification(
        {
            "schema_version": 1,
            "collected_at_epoch": 1_000,
            "carriers": {"telecom": {"tested": 10, "reachable": 9, "median_latency_ms": 40}},
        },
        now_epoch=1_000 + 30,
    )
    assert populated["authority"] == "external_self_hosted_advisory"
