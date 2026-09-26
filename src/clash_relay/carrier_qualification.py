"""Optional, pluggable carrier-qualification data boundary.

GitHub Runner endpoint qualification only filters obviously dead TCP
endpoints from a US data-center vantage point; it must never be read as China
Telecom / Unicom / Mobile reachability. True carrier quality can only come
from self-hosted probes on those access networks.

The separate self-hosted producer submits an aggregate-only payload
(``parse_carrier_aggregate_payload``) or
in-process :class:`CarrierProbeResult` rows; ``run_carrier_qualification``
reduces them to an aggregate-only report and defaults to ``not_configured``.
Validation fails closed on anything beyond per-carrier aggregate numbers, so
hostnames, IPs, node names, subscription identities, or raw samples can never
cross this boundary.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import ValidationError

_CARRIERS = frozenset({"telecom", "unicom", "mobile"})
_PAYLOAD_SCHEMA_VERSION = 1
_PAYLOAD_KEYS = frozenset({"schema_version", "carriers", "collected_at_epoch", "sample_set_id"})
_ROW_KEYS = frozenset(
    {
        "tested",
        "reachable",
        "median_latency_ms",
        "sampled",
        "skipped_unsupported",
        "p90_latency_ms",
        "sufficient_evidence",
    }
)
_MAX_RESULT_AGE_SECONDS = 6 * 3600
_MIN_SAMPLES_PER_CARRIER = 5
MIN_SAMPLES_PER_CARRIER = _MIN_SAMPLES_PER_CARRIER
_SAMPLE_SET_ID_PATTERN = re.compile(r"^[0-9a-f]{16,64}$")
_AUTHORITY = "external_self_hosted_advisory"


def parse_carrier_json_text(text: str) -> dict[str, Any]:
    """Reject duplicate JSON keys before an aggregate mapping can overwrite them."""

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValidationError("carrier qualification JSON repeats a field")
            result[key] = value
        return result

    try:
        payload = json.loads(text, object_pairs_hook=unique_object)
    except json.JSONDecodeError as exc:
        raise ValidationError("carrier qualification input is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationError("carrier qualification input must be an object")
    return payload


class CarrierProbeResult:
    """One carrier probe stage's aggregate measurement of its own network.

    ``tested``/``reachable`` count probe samples the self-hosted probe already
    aggregated locally; ``median_latency_ms`` is that probe's own aggregate.
    The stage never passes raw endpoints into this boundary.
    """

    __slots__ = (
        "carrier",
        "median_latency_ms",
        "p90_latency_ms",
        "reachable",
        "sampled",
        "skipped_unsupported",
        "sufficient",
        "tested",
    )

    def __init__(
        self,
        *,
        carrier: str,
        tested: object,
        reachable: object,
        median_latency_ms: object,
        sampled: object = None,
        skipped_unsupported: object = None,
        p90_latency_ms: object = None,
    ) -> None:
        if carrier not in _CARRIERS:
            raise ValidationError(
                f"carrier qualification requires a known carrier, got {carrier!r}"
            )
        if (
            not isinstance(tested, int)
            or isinstance(tested, bool)
            or tested < (0 if sampled is not None else 1)
        ):
            raise ValidationError("carrier qualification requires a positive sample count")
        if (
            not isinstance(reachable, int)
            or isinstance(reachable, bool)
            or not 0 <= reachable <= tested
        ):
            raise ValidationError("carrier qualification requires reachable within tested samples")
        if median_latency_ms is None and reachable == 0:
            pass
        elif (
            not isinstance(median_latency_ms, (int, float))
            or isinstance(median_latency_ms, bool)
            or not math.isfinite(median_latency_ms)
            or median_latency_ms < 0
            or reachable == 0
        ):
            raise ValidationError("carrier qualification requires a numeric median latency")
        if sampled is not None and (
            not isinstance(sampled, int) or isinstance(sampled, bool) or sampled < tested
        ):
            raise ValidationError("carrier qualification sampled count is invalid")
        if skipped_unsupported is not None and (
            not isinstance(skipped_unsupported, int)
            or isinstance(skipped_unsupported, bool)
            or skipped_unsupported < 0
        ):
            raise ValidationError("carrier qualification skipped count is invalid")
        if p90_latency_ms is not None and (
            not isinstance(p90_latency_ms, (int, float))
            or isinstance(p90_latency_ms, bool)
            or not math.isfinite(p90_latency_ms)
            or p90_latency_ms < 0
            or reachable == 0
        ):
            raise ValidationError("carrier qualification p90 latency is invalid")
        self.carrier = carrier
        self.tested = int(tested)
        self.reachable = int(reachable)
        self.median_latency_ms = float(median_latency_ms) if median_latency_ms is not None else None
        self.sampled = int(sampled) if sampled is not None else None
        self.skipped_unsupported = (
            int(skipped_unsupported) if skipped_unsupported is not None else None
        )
        self.p90_latency_ms = float(p90_latency_ms) if p90_latency_ms is not None else None
        self.sufficient = (
            self.sampled is not None
            and self.sampled >= _MIN_SAMPLES_PER_CARRIER
            and self.tested == self.sampled
        )

    def as_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "carrier": self.carrier,
            "tested": self.tested,
            "reachable": self.reachable,
            "median_latency_ms": round(self.median_latency_ms, 3)
            if self.median_latency_ms is not None
            else None,
        }
        if self.sampled is not None:
            row["sampled"] = self.sampled
            row["sufficient_evidence"] = self.sufficient
        if self.skipped_unsupported is not None:
            row["skipped_unsupported"] = self.skipped_unsupported
        if self.p90_latency_ms is not None:
            row["p90_latency_ms"] = round(self.p90_latency_ms, 3)
        return row


def aggregate_carrier_results(results: Sequence[CarrierProbeResult]) -> dict[str, Any]:
    """Reduce per-carrier probe rows to an aggregate-only carrier report."""

    rows = list(results)
    if not rows:
        return {"status": "not_configured", "authority": _AUTHORITY, "carriers": {}}
    carriers: dict[str, dict[str, Any]] = {}
    tested_total = 0
    reachable_total = 0
    for row in rows:
        if row.carrier in carriers:
            raise ValidationError("carrier qualification repeats a carrier")
        carriers[row.carrier] = row.as_dict()
        tested_total += row.tested
        reachable_total += row.reachable
    return {
        "status": "full" if set(carriers) == _CARRIERS else "partial",
        "coverage": "full" if set(carriers) == _CARRIERS else "partial",
        "evidence": _evidence_block(rows),
        "authority": _AUTHORITY,
        "freshness": {"status": "current", "source": "in_process"},
        "carriers": dict(sorted(carriers.items())),
        "aggregate": {
            "carriers_reported": len(carriers),
            "tested": tested_total,
            "reachable": reachable_total,
            "reachable_ratio": round(reachable_total / tested_total, 4) if tested_total else 0.0,
        },
    }


def parse_carrier_aggregate_payload(
    payload: Mapping[str, Any],
) -> tuple[list[CarrierProbeResult], int, str | None]:
    """Validate one self-hosted aggregate payload and return probe rows.

    Accepted shape::

        {"schema_version": 1, "collected_at_epoch": 1760000000,
         "carriers": {"telecom": {"tested": 40, "reachable": 38,
         "median_latency_ms": 52.4}, ...}}  # collected_at_epoch required

    ``collected_at_epoch`` is required so stale input can never pose as
    passed evidence. Anything else — unknown carriers, unknown row or payload
    keys, negative or non-integer counts, raw sample lists, or
    identity-bearing fields — fails closed. Carriers are optional and may
    cover any subset.
    """

    if not isinstance(payload, Mapping):
        raise ValidationError("carrier qualification payload must be an object")
    unknown_payload_keys = set(payload) - _PAYLOAD_KEYS
    if unknown_payload_keys:
        raise ValidationError(
            "carrier qualification payload rejects unknown fields: "
            + ", ".join(sorted(unknown_payload_keys))
        )
    if payload.get("schema_version") != _PAYLOAD_SCHEMA_VERSION:
        raise ValidationError(
            f"carrier qualification payload requires schema_version {_PAYLOAD_SCHEMA_VERSION}"
        )
    carriers = payload.get("carriers")
    if not isinstance(carriers, Mapping) or not carriers:
        raise ValidationError("carrier qualification payload requires carrier rows")
    rows: list[CarrierProbeResult] = []
    for carrier, row in carriers.items():
        if carrier not in _CARRIERS:
            raise ValidationError(f"carrier qualification requires known carriers, got {carrier!r}")
        if not isinstance(row, Mapping):
            raise ValidationError(f"carrier qualification row {carrier!r} must be an object")
        unknown_row_keys = set(row) - _ROW_KEYS
        if unknown_row_keys:
            raise ValidationError(
                f"carrier qualification row {carrier!r} rejects unknown fields: "
                + ", ".join(sorted(unknown_row_keys))
            )
        rows.append(
            CarrierProbeResult(
                carrier=str(carrier),
                tested=row.get("tested"),
                reachable=row.get("reachable"),
                median_latency_ms=row.get("median_latency_ms"),
                sampled=row.get("sampled"),
                skipped_unsupported=row.get("skipped_unsupported"),
                p90_latency_ms=row.get("p90_latency_ms"),
            )
        )
    seen = {row.carrier for row in rows}
    if len(seen) != len(rows):
        raise ValidationError("carrier qualification payload repeats a carrier")
    if "collected_at_epoch" not in payload:
        raise ValidationError("carrier qualification payload requires collected_at_epoch")
    collected = payload["collected_at_epoch"]
    if not isinstance(collected, int) or isinstance(collected, bool):
        raise ValidationError("carrier qualification collected_at_epoch must be an integer epoch")
    sample_set_id = payload.get("sample_set_id")
    if sample_set_id is not None and (
        not isinstance(sample_set_id, str)
        or _SAMPLE_SET_ID_PATTERN.fullmatch(sample_set_id) is None
    ):
        raise ValidationError(
            "carrier qualification sample_set_id must be 16-64 lowercase hex digits"
        )
    # Producer-declared sufficiency must agree with the sample counts; drift
    # means the payload was hand-edited and fails closed.
    for carrier, row in carriers.items():
        claimed = row.get("sufficient_evidence")
        sampled = row.get("sampled")
        if claimed is None and sampled is None:
            continue  # legacy aggregate is accepted but insufficient
        expected = (
            isinstance(sampled, int)
            and not isinstance(sampled, bool)
            and sampled >= _MIN_SAMPLES_PER_CARRIER
            and row.get("tested") == sampled
        )
        if not isinstance(claimed, bool) or claimed != expected:
            raise ValidationError(
                f"carrier qualification sufficiency evidence drifted for {carrier!r}"
            )
    return rows, collected, sample_set_id


def _evidence_block(rows: Sequence[CarrierProbeResult]) -> dict[str, Any]:
    """Evidence quality for the reporting carriers, separate from coverage."""

    insufficient = [row.carrier for row in rows if not row.sufficient]
    return {
        "status": "sufficient" if rows and not insufficient else "insufficient",
        "minimum_samples_per_carrier": _MIN_SAMPLES_PER_CARRIER,
        "insufficient_carriers": sorted(insufficient),
    }


def run_carrier_qualification(
    results: Sequence[CarrierProbeResult] | Mapping[str, Any] | None = None,
    *,
    now_epoch: int | None = None,
) -> dict[str, Any]:
    """Entry point the qualification pipeline calls for carrier evidence.

    Without input the report stays ``not_configured``. A self-hosted probe
    stage passes either an aggregate payload mapping or
    :class:`CarrierProbeResult` rows; only the aggregate reduction is
    published. A payload carrying ``collected_at_epoch`` older than
    ``_MAX_RESULT_AGE_SECONDS`` is reported as ``stale`` — its aggregates are
    not passed evidence.
    """

    if results is None or (
        isinstance(results, Sequence) and not isinstance(results, (str, bytes)) and not results
    ):
        return {
            "status": "not_configured",
            "authority": _AUTHORITY,
            "carriers": {},
            "note": (
                "carrier qualification requires external self-hosted evidence; global endpoint "
                "preflight results must not be read as carrier quality"
            ),
        }
    if isinstance(results, Mapping):
        rows, collected, sample_set_id = parse_carrier_aggregate_payload(results)
        report = aggregate_carrier_results(rows)
        if sample_set_id is not None:
            report["sample_set_id"] = sample_set_id
        evidence = _evidence_block(rows)
        report["evidence"] = evidence
        if isinstance(collected, int) and not isinstance(collected, bool):
            now = int(time.time()) if now_epoch is None else int(now_epoch)
            if collected > now:
                raise ValidationError("carrier qualification timestamp is in the future")
            age_seconds = max(now - collected, 0)
            report["freshness"] = {
                "collected_at_epoch": collected,
                "age_seconds": age_seconds,
                "max_age_seconds": _MAX_RESULT_AGE_SECONDS,
                "status": "current" if age_seconds <= _MAX_RESULT_AGE_SECONDS else "stale",
            }
            if age_seconds > _MAX_RESULT_AGE_SECONDS:
                report["status"] = "stale"
                evidence["status"] = "stale"
        return report
    rows = list(results)
    if any(not isinstance(row, CarrierProbeResult) for row in rows):
        raise ValidationError("carrier qualification accepts CarrierProbeResult rows only")
    if not rows:
        return {
            "status": "not_configured",
            "authority": _AUTHORITY,
            "carriers": {},
            "note": (
                "carrier qualification requires external self-hosted evidence; global endpoint "
                "preflight results must not be read as carrier quality"
            ),
        }
    return aggregate_carrier_results(rows)


def safe_carrier_report(value: object) -> dict[str, Any]:
    """Project only validated carrier aggregates into public observability."""
    if not isinstance(value, Mapping):
        raise ValidationError("carrier qualification report must be an object")
    status = value.get("status")
    if status not in {"not_configured", "partial", "full", "stale"}:
        raise ValidationError("carrier qualification report has an invalid status")
    if value.get("authority") != _AUTHORITY:
        raise ValidationError("carrier qualification report has an invalid authority")
    raw_carriers = value.get("carriers")
    if not isinstance(raw_carriers, Mapping):
        raise ValidationError("carrier qualification report has invalid carrier rows")
    raw_sample_set_id = value.get("sample_set_id")
    if raw_sample_set_id is not None and (
        not isinstance(raw_sample_set_id, str)
        or _SAMPLE_SET_ID_PATTERN.fullmatch(raw_sample_set_id) is None
    ):
        raise ValidationError("carrier qualification sample_set_id is invalid")
    carriers: dict[str, dict[str, Any]] = {}
    for carrier, row in sorted(raw_carriers.items()):
        if carrier not in _CARRIERS or not isinstance(row, Mapping):
            raise ValidationError("carrier qualification report has an invalid carrier")
        verified = CarrierProbeResult(
            carrier=carrier,
            tested=row.get("tested"),
            reachable=row.get("reachable"),
            median_latency_ms=row.get("median_latency_ms"),
            sampled=row.get("sampled"),
            skipped_unsupported=row.get("skipped_unsupported"),
            p90_latency_ms=row.get("p90_latency_ms"),
        )
        carrier_row: dict[str, Any] = {
            "tested": verified.tested,
            "reachable": verified.reachable,
            "median_latency_ms": round(verified.median_latency_ms, 3)
            if verified.median_latency_ms is not None
            else None,
        }
        if verified.sampled is not None:
            carrier_row["sampled"] = verified.sampled
            carrier_row["sufficient_evidence"] = verified.sufficient
        if verified.skipped_unsupported is not None:
            carrier_row["skipped_unsupported"] = verified.skipped_unsupported
        if verified.p90_latency_ms is not None:
            carrier_row["p90_latency_ms"] = round(verified.p90_latency_ms, 3)
        carriers[carrier] = carrier_row
    safe: dict[str, Any] = {
        "status": status,
        "authority": _AUTHORITY,
        "carriers": carriers,
    }
    if raw_sample_set_id is not None:
        safe["sample_set_id"] = raw_sample_set_id
    if status == "not_configured":
        if carriers:
            raise ValidationError("unconfigured carrier evidence contains rows")
        return safe
    aggregate = value.get("aggregate")
    freshness = value.get("freshness")
    if not isinstance(aggregate, Mapping) or not isinstance(freshness, Mapping):
        raise ValidationError("carrier qualification report lacks aggregate freshness")
    expected_tested = sum(row["tested"] for row in carriers.values())
    expected_reachable = sum(row["reachable"] for row in carriers.values())
    expected_ratio = round(expected_reachable / expected_tested, 4) if expected_tested else 0.0
    if (
        aggregate.get("carriers_reported") != len(carriers)
        or aggregate.get("tested") != expected_tested
        or aggregate.get("reachable") != expected_reachable
        or aggregate.get("reachable_ratio") != expected_ratio
    ):
        raise ValidationError("carrier qualification aggregate counts drifted")
    freshness_status = freshness.get("status")
    if freshness_status not in {"current", "stale"}:
        raise ValidationError("carrier qualification freshness is invalid")
    expected_status = (
        "stale"
        if freshness_status == "stale"
        else "full"
        if set(carriers) == _CARRIERS
        else "partial"
    )
    if status != expected_status:
        raise ValidationError("carrier qualification coverage status drifted")
    safe["coverage"] = "full" if set(carriers) == _CARRIERS else "partial"
    safe["aggregate"] = {
        "carriers_reported": len(carriers),
        "tested": expected_tested,
        "reachable": expected_reachable,
        "reachable_ratio": expected_ratio,
    }
    safe["freshness"] = {"status": freshness_status}
    sampled_carriers = [
        carrier for carrier, row in carriers.items() if row.get("sampled") is not None
    ]
    sufficient = len(sampled_carriers) == len(carriers) and all(
        row.get("sufficient_evidence")
        for carrier, row in carriers.items()
        if row.get("sampled") is not None
    )
    if freshness_status == "stale":
        evidence_status = "stale"
    elif sufficient:
        evidence_status = "sufficient"
    else:
        evidence_status = "insufficient"
    safe["evidence"] = {
        "status": evidence_status,
        "minimum_samples_per_carrier": _MIN_SAMPLES_PER_CARRIER,
        "insufficient_carriers": sorted(
            carrier for carrier, row in carriers.items() if row.get("sufficient_evidence") is False
        ),
    }
    return safe
