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

import time
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import ValidationError

_CARRIERS = frozenset({"telecom", "unicom", "mobile"})
_PAYLOAD_SCHEMA_VERSION = 1
_PAYLOAD_KEYS = frozenset({"schema_version", "carriers", "collected_at_epoch"})
_ROW_KEYS = frozenset({"tested", "reachable", "median_latency_ms"})
_MAX_RESULT_AGE_SECONDS = 6 * 3600
_CLOCK_SKEW_SECONDS = 300


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
        if not isinstance(median_latency_ms, (int, float)) or isinstance(median_latency_ms, bool):
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
        return {"status": "not_configured", "carriers": {}}
    carriers: dict[str, dict[str, Any]] = {}
    tested_total = 0
    reachable_total = 0
    for row in rows:
        carriers[row.carrier] = row.as_dict()
        tested_total += row.tested
        reachable_total += row.reachable
    latency = sorted(row.median_latency_ms for row in rows)
    middle = len(latency) // 2
    median_latency = (
        latency[middle]
        if len(latency) % 2
        else round((latency[middle - 1] + latency[middle]) / 2, 3)
    )
    return {
        "status": "passed",
        "carriers": dict(sorted(carriers.items())),
        "aggregate": {
            "carriers_reported": len(rows),
            "tested": tested_total,
            "reachable": reachable_total,
            "reachable_ratio": round(reachable_total / tested_total, 4) if tested_total else 0.0,
            "median_latency_ms": median_latency,
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
            "carriers": {},
            "note": (
                "carrier qualification is a reserved extension point; global endpoint "
                "preflight results must not be read as carrier quality"
            ),
        }
    if isinstance(results, Mapping):
        report = aggregate_carrier_results(parse_carrier_aggregate_payload(results))
        collected = results.get("collected_at_epoch")
        if isinstance(collected, int) and not isinstance(collected, bool):
            now = int(time.time()) if now_epoch is None else int(now_epoch)
            if collected > now + _CLOCK_SKEW_SECONDS:
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
            "carriers": {},
            "note": (
                "carrier qualification is a reserved extension point; global endpoint "
                "preflight results must not be read as carrier quality"
            ),
        }
    return aggregate_carrier_results(rows)
