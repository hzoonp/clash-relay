from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaValidationError

from clash_relay.carrier_qualification import (
    CarrierProbeResult,
    aggregate_carrier_results,
    parse_carrier_aggregate_payload,
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

    assert report["status"] == "passed"
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

    assert report["status"] == "passed"
    assert "endpoints" not in serialized
    assert "samples" not in serialized
    assert "probe_server" not in serialized


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
    assert current["status"] == "passed"
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


def _v2_payload() -> dict:
    return {
        "schema_version": 2,
        "profile": "cn_three_net",
        "collected_at_epoch": 10_000,
        "window": {"start_epoch": 9_400, "end_epoch": 10_000},
        "carriers": {
            "telecom": {
                "tested": 40,
                "reachable": 38,
                "median_latency_ms": 52.4,
                "failures": {"suppressed": 2},
                "by_region": {
                    "hk": {"tested": 20, "reachable": 19},
                    "jp": {"tested": 20, "reachable": 19},
                },
                "by_protocol": {"trojan": {"tested": 40, "reachable": 38}},
            }
        },
    }


def test_v2_carrier_evidence_is_aggregated_and_windowed() -> None:
    report = run_carrier_qualification(_v2_payload(), now_epoch=10_060)
    assert report["status"] == "passed"
    assert report["schema_version"] == 2
    assert report["profile"] == "cn_three_net"
    assert report["window"] == {"start_epoch": 9_400, "end_epoch": 10_000}
    assert report["carriers"]["telecom"]["failures"] == {"suppressed": 2}
    assert report["carriers"]["telecom"]["by_region"]["hk"]["tested"] == 20


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (lambda p: p.update(profile="secret-profile"), "known network profile"),
        (lambda p: p["window"].update(end_epoch=10_001), "collection time"),
        (lambda p: p["carriers"]["telecom"].update(server="1.2.3.4"), "unknown fields"),
        (
            lambda p: p["carriers"]["telecom"]["by_region"].update(
                us={"tested": 1, "reachable": 1}
            ),
            "small or invalid cell",
        ),
        (lambda p: p["carriers"]["telecom"].update(failures={"timeout": 2}), "small cell"),
        (lambda p: p.update(node_name="secret"), "unknown fields"),
    ],
)
def test_v2_rejects_identity_and_small_detail_cells(change, match: str) -> None:
    payload = _v2_payload()
    change(payload)
    with pytest.raises(ValidationError, match=match):
        run_carrier_qualification(payload, now_epoch=10_060)


def test_versioned_carrier_schema_accepts_legacy_and_v2_evidence(repo_root) -> None:
    schema = json.loads((repo_root / "schemas/carrier-evidence.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    legacy = {
        "schema_version": 1,
        "collected_at_epoch": 10_000,
        "carriers": {"telecom": {"tested": 10, "reachable": 9, "median_latency_ms": 20.0}},
    }

    validator.validate(legacy)
    validator.validate(_v2_payload())
    legacy["carriers"]["telecom"]["server"] = "private.invalid.example"
    with pytest.raises(SchemaValidationError):
        validator.validate(legacy)
