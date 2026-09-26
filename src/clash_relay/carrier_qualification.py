"""Optional, pluggable carrier-qualification data boundary.

GitHub Runner endpoint qualification only filters obviously dead TCP
endpoints from a US data-center vantage point; it must never be read as China
Telecom / Unicom / Mobile reachability. True carrier quality can only come
from self-hosted probes on those access networks.

The repository ships no real probes. A CI job or self-hosted probe operator
submits an aggregate-only payload (``parse_carrier_aggregate_payload``) or
in-process :class:`CarrierProbeResult` rows; ``run_carrier_qualification``
reduces them to an aggregate-only report and defaults to ``not_configured``.
Validation fails closed on anything beyond per-carrier aggregate numbers, so
hostnames, IPs, node names, subscription identities, or raw samples can never
cross this boundary.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import ValidationError

_CARRIERS = frozenset({"telecom", "unicom", "mobile"})
_PAYLOAD_SCHEMA_VERSION = 1
_PAYLOAD_KEYS = frozenset({"schema_version", "carriers", "collected_at_epoch"})
_ROW_KEYS = frozenset({"tested", "reachable", "median_latency_ms"})
_MAX_RESULT_AGE_SECONDS = 6 * 3600
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

    __slots__ = ("carrier", "median_latency_ms", "reachable", "tested")

    def __init__(
        self, *, carrier: str, tested: object, reachable: object, median_latency_ms: object
    ) -> None:
        if carrier not in _CARRIERS:
            raise ValidationError(
                f"carrier qualification requires a known carrier, got {carrier!r}"
            )
        if not isinstance(tested, int) or isinstance(tested, bool) or tested < 1:
            raise ValidationError("carrier qualification requires a positive sample count")
        if (
            not isinstance(reachable, int)
            or isinstance(reachable, bool)
            or not 0 <= reachable <= tested
        ):
            raise ValidationError("carrier qualification requires reachable within tested samples")
        if (
            not isinstance(median_latency_ms, (int, float))
            or isinstance(median_latency_ms, bool)
            or not math.isfinite(median_latency_ms)
            or median_latency_ms < 0
        ):
            raise ValidationError("carrier qualification requires a numeric median latency")
        self.carrier = carrier
        self.tested = int(tested)
        self.reachable = int(reachable)
        self.median_latency_ms = float(median_latency_ms)

    def as_dict(self) -> dict[str, Any]:
        return {
            "carrier": self.carrier,
            "tested": self.tested,
            "reachable": self.reachable,
            "median_latency_ms": round(self.median_latency_ms, 3),
        }


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


def parse_carrier_aggregate_payload(payload: Mapping[str, Any]) -> list[CarrierProbeResult]:
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
            )
        )
    seen = {row.carrier for row in rows}
    if len(seen) != len(rows):
        raise ValidationError("carrier qualification payload repeats a carrier")
    if "collected_at_epoch" not in payload:
        raise ValidationError("carrier qualification payload requires collected_at_epoch")
    if "collected_at_epoch" in payload:
        collected = payload["collected_at_epoch"]
        if not isinstance(collected, int) or isinstance(collected, bool):
            raise ValidationError(
                "carrier qualification collected_at_epoch must be an integer epoch"
            )
    return rows


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
        report = aggregate_carrier_results(parse_carrier_aggregate_payload(results))
        collected = results.get("collected_at_epoch")
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
    carriers: dict[str, dict[str, int | float]] = {}
    for carrier, row in sorted(raw_carriers.items()):
        if carrier not in _CARRIERS or not isinstance(row, Mapping):
            raise ValidationError("carrier qualification report has an invalid carrier")
        verified = CarrierProbeResult(
            carrier=carrier,
            tested=row.get("tested"),
            reachable=row.get("reachable"),
            median_latency_ms=row.get("median_latency_ms"),
        )
        carriers[carrier] = {
            "tested": verified.tested,
            "reachable": verified.reachable,
            "median_latency_ms": round(verified.median_latency_ms, 3),
        }
    safe: dict[str, Any] = {
        "status": status,
        "authority": _AUTHORITY,
        "carriers": carriers,
    }
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
    safe["aggregate"] = {
        "carriers_reported": len(carriers),
        "tested": expected_tested,
        "reachable": expected_reachable,
        "reachable_ratio": expected_ratio,
    }
    safe["freshness"] = {"status": freshness_status}
    return safe
