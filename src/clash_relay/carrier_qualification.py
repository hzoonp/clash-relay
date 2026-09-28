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

import math
import time
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import ValidationError

_CARRIERS = frozenset({"telecom", "unicom", "mobile"})
_PAYLOAD_KEYS = frozenset({"schema_version", "carriers", "collected_at_epoch"})
_PAYLOAD_V2_KEYS = _PAYLOAD_KEYS | {"profile", "window"}
_ROW_KEYS = frozenset({"tested", "reachable", "median_latency_ms"})
_ROW_V2_KEYS = _ROW_KEYS | {"failures", "by_region", "by_protocol"}
_PROFILES = frozenset({"default", "cn_three_net"})
_FAILURES = frozenset({"timeout", "dns", "tls", "connection", "http", "other", "suppressed"})
_REGIONS = frozenset({"hk", "tw", "sg", "jp", "us", "kr", "uk", "other"})
_PROTOCOLS = frozenset(
    {
        "ss",
        "ssr",
        "vmess",
        "vless",
        "trojan",
        "http",
        "socks5",
        "snell",
        "hysteria",
        "hysteria2",
        "tuic",
        "anytls",
        "wireguard",
        "ssh",
        "mieru",
        "masque",
        "other",
    }
)
_MIN_DETAIL_SAMPLES = 10
_MAX_WINDOW_SECONDS = 6 * 3600
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
    version = payload.get("schema_version")
    unknown_payload_keys = set(payload) - (_PAYLOAD_V2_KEYS if version == 2 else _PAYLOAD_KEYS)
    if unknown_payload_keys:
        raise ValidationError(
            "carrier qualification payload rejects unknown fields: "
            + ", ".join(sorted(unknown_payload_keys))
        )
    if not isinstance(version, int) or isinstance(version, bool) or version not in (1, 2):
        raise ValidationError("carrier qualification payload requires schema_version 1 or 2")
    if version == 2:
        _validate_v2_metadata(payload)
    carriers = payload.get("carriers")
    if not isinstance(carriers, Mapping) or not carriers:
        raise ValidationError("carrier qualification payload requires carrier rows")
    rows: list[CarrierProbeResult] = []
    for carrier, row in carriers.items():
        if carrier not in _CARRIERS:
            raise ValidationError(f"carrier qualification requires known carriers, got {carrier!r}")
        if not isinstance(row, Mapping):
            raise ValidationError(f"carrier qualification row {carrier!r} must be an object")
        unknown_row_keys = set(row) - (_ROW_KEYS if version == 1 else _ROW_V2_KEYS)
        if unknown_row_keys:
            raise ValidationError(
                f"carrier qualification row {carrier!r} rejects unknown fields: "
                + ", ".join(sorted(unknown_row_keys))
            )
        if version == 2:
            _validate_v2_row(row)
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


def _count(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValidationError(f"carrier qualification {label} must be a non-negative count")
    return value


def _validate_v2_metadata(payload: Mapping[str, Any]) -> None:
    profile = payload.get("profile")
    if not isinstance(profile, str) or profile not in _PROFILES:
        raise ValidationError("carrier qualification v2 requires a known network profile")
    window = payload.get("window")
    if not isinstance(window, Mapping) or set(window) != {"start_epoch", "end_epoch"}:
        raise ValidationError("carrier qualification v2 requires a bounded time window")
    start = _count(window["start_epoch"], "window.start_epoch")
    end = _count(window["end_epoch"], "window.end_epoch")
    if not 0 < end - start <= _MAX_WINDOW_SECONDS:
        raise ValidationError("carrier qualification v2 time window is invalid")
    if payload.get("collected_at_epoch") != end:
        raise ValidationError("carrier qualification v2 collection time must equal window end")


def _validate_breakdown(
    value: object, *, label: str, allowed: frozenset[str], maximum: int
) -> None:
    if not isinstance(value, Mapping) or not value:
        raise ValidationError(f"carrier qualification {label} must have aggregate rows")
    total = 0
    for category, row in value.items():
        if category not in allowed:
            raise ValidationError(f"carrier qualification {label} has an unsupported category")
        if not isinstance(row, Mapping) or set(row) != {"tested", "reachable"}:
            raise ValidationError(f"carrier qualification {label} requires aggregate counts")
        tested = _count(row["tested"], f"{label}.tested")
        reachable = _count(row["reachable"], f"{label}.reachable")
        if tested < _MIN_DETAIL_SAMPLES or reachable > tested:
            raise ValidationError(f"carrier qualification {label} has a small or invalid cell")
        total += tested
    if total > maximum:
        raise ValidationError(f"carrier qualification {label} exceeds carrier samples")


def _validate_v2_row(row: Mapping[str, Any]) -> None:
    tested = _count(row.get("tested"), "tested")
    reachable = _count(row.get("reachable"), "reachable")
    if tested < _MIN_DETAIL_SAMPLES or reachable > tested:
        raise ValidationError("carrier qualification v2 requires at least ten aggregate samples")
    failures = row.get("failures")
    if not isinstance(failures, Mapping) or set(failures) - _FAILURES:
        raise ValidationError("carrier qualification v2 requires allowed failure categories")
    for category, value in failures.items():
        count = _count(value, "failure category")
        if category != "suppressed" and 0 < count < _MIN_DETAIL_SAMPLES:
            raise ValidationError("carrier qualification failure category has a small cell")
    if sum(failures.values()) != tested - reachable:
        raise ValidationError(
            "carrier qualification failure counts must equal unsuccessful samples"
        )
    for label, allowed in (("by_region", _REGIONS), ("by_protocol", _PROTOCOLS)):
        if label in row:
            _validate_breakdown(row[label], label=label, allowed=allowed, maximum=tested)


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
        if results.get("schema_version") == 2:
            report["schema_version"] = 2
            report["profile"] = results["profile"]
            report["window"] = dict(results["window"])
            for carrier, row in results["carriers"].items():
                report["carriers"][carrier]["failures"] = dict(sorted(row["failures"].items()))
                for label in ("by_region", "by_protocol"):
                    if label in row:
                        report["carriers"][carrier][label] = {
                            name: {"tested": detail["tested"], "reachable": detail["reachable"]}
                            for name, detail in sorted(row[label].items())
                        }
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
