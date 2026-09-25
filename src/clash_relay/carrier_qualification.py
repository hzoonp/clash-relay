"""Reserved carrier-qualification extension interface.

GitHub Runner endpoint qualification only filters obviously dead TCP
endpoints from a US data-center vantage point; it must never be read as China
Telecom / Unicom / Mobile reachability. True carrier quality can only come
from self-hosted probes on those access networks.

This module reserves the aggregation boundary for that future stage. A probe
stage submits per-carrier :class:`CarrierProbeResult` rows; this module
reduces them to an aggregate-only report. No endpoint, hostname, or raw
sample ever crosses this boundary, so future carrier probes cannot leak
private runtime identities into published reports.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .errors import ValidationError

_CARRIERS = frozenset({"telecom", "unicom", "mobile"})


class CarrierProbeResult:
    """One carrier probe stage's aggregate measurement of its own network.

    ``tested``/``reachable`` count probe samples the self-hosted probe already
    aggregated locally; ``median_latency_ms`` is that probe's own aggregate.
    The stage never passes raw endpoints into this boundary.
    """

    __slots__ = ("carrier", "median_latency_ms", "reachable", "tested")

    def __init__(
        self, *, carrier: str, tested: int, reachable: int, median_latency_ms: float
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
        self.tested = tested
        self.reachable = reachable
        self.median_latency_ms = float(median_latency_ms)

    def as_dict(self) -> dict[str, Any]:
        return {
            "carrier": self.carrier,
            "tested": self.tested,
            "reachable": self.reachable,
            "median_latency_ms": round(float(self.median_latency_ms), 3),
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


def run_carrier_qualification(
    results: Sequence[CarrierProbeResult] | None = None,
) -> dict[str, Any]:
    """Entry point the qualification pipeline calls for carrier evidence.

    This phase reserves the interface without introducing real Telecom,
    Unicom, or Mobile probes: an empty call reports ``not_configured``. A
    future self-hosted probe stage passes :class:`CarrierProbeResult` rows and
    only the aggregate reduction is published.
    """

    if not results:
        return {
            "status": "not_configured",
            "carriers": {},
            "note": (
                "carrier qualification is a reserved extension point; global endpoint "
                "preflight results must not be read as carrier quality"
            ),
        }
    rows = list(results)
    if any(not isinstance(row, CarrierProbeResult) for row in rows):
        raise ValidationError("carrier qualification accepts CarrierProbeResult rows only")
    return aggregate_carrier_results(rows)
