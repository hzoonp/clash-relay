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
from typing import Any, cast

from .errors import ValidationError

_CARRIERS = frozenset({"telecom", "unicom", "mobile"})
_PAYLOAD_SCHEMA_VERSION = 1
_PAYLOAD_KEYS = frozenset(
    {
        "schema_version",
        "carriers",
        "collected_at_epoch",
        "sample_set_id",
        "inventory_set_id",
        "probe_plan_id",
        "sampler_version",
    }
)
_ROW_KEYS = frozenset(
    {
        "tested",
        "reachable",
        "median_latency_ms",
        "sampled",
        "skipped_unsupported",
        "p90_latency_ms",
        "sufficient_evidence",
        "sampled_tcp_endpoints",
        "skipped_udp_native_endpoints",
        "geographic_regions_sampled",
        "protocols_sampled",
        "sources_sampled",
        "strata_sampled",
        "eligible_tcp_endpoints",
        "outcomes",
    }
)
_MAX_RESULT_AGE_SECONDS = 6 * 3600
_MIN_SAMPLES_PER_CARRIER = 5
MIN_SAMPLES_PER_CARRIER = _MIN_SAMPLES_PER_CARRIER
SAMPLER_VERSION = 3
MAX_CARRIER_SAMPLES = 48
MAX_UDP_NATIVE_ENDPOINTS = 100_000
MAX_ELIGIBLE_ENDPOINTS = 100_000
MAX_FALLBACK_INVENTORY = 12
MAX_LATENCY_MS = 60_000
OUTCOME_CATEGORIES = frozenset(
    {
        "dns_failure",
        "connect_timeout",
        "connection_refused",
        "connect_failure",
        "tcp_connected",
    }
)
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
        "eligible_tcp_endpoints",
        "geographic_regions_sampled",
        "median_latency_ms",
        "outcomes",
        "p90_latency_ms",
        "protocols_sampled",
        "reachable",
        "sampled",
        "sampled_tcp_endpoints",
        "skipped_udp_native_endpoints",
        "skipped_unsupported",
        "sources_sampled",
        "strata_sampled",
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
        sampled_tcp_endpoints: object = None,
        skipped_udp_native_endpoints: object = None,
        geographic_regions_sampled: object = None,
        protocols_sampled: object = None,
        sources_sampled: object = None,
        strata_sampled: object = None,
        eligible_tcp_endpoints: object = None,
        outcomes: object = None,
    ) -> None:
        if carrier not in _CARRIERS:
            raise ValidationError(
                f"carrier qualification requires a known carrier, got {carrier!r}"
            )
        if (
            not isinstance(tested, int)
            or isinstance(tested, bool)
            or not (0 if sampled is not None else 1) <= tested <= MAX_CARRIER_SAMPLES
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
            or median_latency_ms > MAX_LATENCY_MS
            or reachable == 0
        ):
            raise ValidationError("carrier qualification requires a numeric median latency")
        if sampled is not None and (
            not isinstance(sampled, int)
            or isinstance(sampled, bool)
            or not tested <= sampled <= MAX_CARRIER_SAMPLES
        ):
            raise ValidationError("carrier qualification sampled count is invalid")
        if skipped_unsupported is not None and (
            not isinstance(skipped_unsupported, int)
            or isinstance(skipped_unsupported, bool)
            or not 0 <= skipped_unsupported <= MAX_UDP_NATIVE_ENDPOINTS
        ):
            raise ValidationError("carrier qualification skipped count is invalid")
        if p90_latency_ms is not None and (
            not isinstance(p90_latency_ms, (int, float))
            or isinstance(p90_latency_ms, bool)
            or not math.isfinite(p90_latency_ms)
            or p90_latency_ms < 0
            or p90_latency_ms > MAX_LATENCY_MS
            or reachable == 0
            or (isinstance(median_latency_ms, (int, float)) and p90_latency_ms < median_latency_ms)
        ):
            raise ValidationError("carrier qualification p90 latency is invalid")
        if sampled_tcp_endpoints is not None and (
            not isinstance(sampled_tcp_endpoints, int)
            or isinstance(sampled_tcp_endpoints, bool)
            or sampled_tcp_endpoints != sampled
        ):
            raise ValidationError("carrier qualification sampled TCP count drifted")
        if skipped_udp_native_endpoints is not None and (
            not isinstance(skipped_udp_native_endpoints, int)
            or isinstance(skipped_udp_native_endpoints, bool)
            or not 0 <= skipped_udp_native_endpoints <= MAX_UDP_NATIVE_ENDPOINTS
            or (
                skipped_unsupported is not None
                and skipped_udp_native_endpoints != skipped_unsupported
            )
        ):
            raise ValidationError("carrier qualification UDP skip count drifted")
        diversity_values = (
            geographic_regions_sampled,
            protocols_sampled,
            sources_sampled,
            strata_sampled,
        )
        if any(value is not None for value in diversity_values):
            if sampled is None or any(
                not isinstance(value, int) or isinstance(value, bool) for value in diversity_values
            ):
                raise ValidationError("carrier qualification diversity counts are incomplete")
            geographic, protocols, sources, strata = (
                cast(int, value) for value in diversity_values
            )
            sampled_count = int(sampled)
            if (
                not 0 <= geographic <= min(7, sampled_count)
                or not (1 if sampled_count else 0) <= protocols <= sampled_count
                or not (1 if sampled_count else 0) <= sources <= sampled_count
                or not (1 if sampled_count else 0) <= strata <= sampled_count
                or strata < max(geographic, protocols, sources)
            ):
                raise ValidationError("carrier qualification diversity counts are invalid")
        if eligible_tcp_endpoints is not None and (
            not isinstance(eligible_tcp_endpoints, int)
            or isinstance(eligible_tcp_endpoints, bool)
            or not (sampled if isinstance(sampled, int) else 0)
            <= eligible_tcp_endpoints
            <= MAX_ELIGIBLE_ENDPOINTS
        ):
            raise ValidationError("carrier qualification eligible endpoint count is invalid")
        if outcomes is not None:
            if not isinstance(outcomes, Mapping) or set(outcomes) != OUTCOME_CATEGORIES:
                raise ValidationError("carrier qualification outcome categories are invalid")
            if (
                any(
                    not isinstance(count, int)
                    or isinstance(count, bool)
                    or not 0 <= count <= tested
                    for count in outcomes.values()
                )
                or sum(outcomes.values()) != tested
                or outcomes["tcp_connected"] != reachable
            ):
                raise ValidationError("carrier qualification outcome counts are invalid")
        self.carrier = carrier
        self.tested = int(tested)
        self.reachable = int(reachable)
        self.median_latency_ms = float(median_latency_ms) if median_latency_ms is not None else None
        self.sampled = int(sampled) if sampled is not None else None
        self.skipped_unsupported = (
            int(skipped_unsupported) if skipped_unsupported is not None else None
        )
        self.p90_latency_ms = float(p90_latency_ms) if p90_latency_ms is not None else None
        self.sampled_tcp_endpoints = sampled_tcp_endpoints
        self.skipped_udp_native_endpoints = skipped_udp_native_endpoints
        self.geographic_regions_sampled = (
            cast(int, geographic_regions_sampled)
            if geographic_regions_sampled is not None
            else None
        )
        self.protocols_sampled = (
            cast(int, protocols_sampled) if protocols_sampled is not None else None
        )
        self.sources_sampled = cast(int, sources_sampled) if sources_sampled is not None else None
        self.strata_sampled = cast(int, strata_sampled) if strata_sampled is not None else None
        self.eligible_tcp_endpoints = (
            cast(int, eligible_tcp_endpoints) if eligible_tcp_endpoints is not None else None
        )
        self.outcomes = dict(sorted(outcomes.items())) if isinstance(outcomes, Mapping) else None
        diverse = (
            self.geographic_regions_sampled is not None
            and self.sources_sampled is not None
            and (self.geographic_regions_sampled >= 2 or self.sources_sampled >= 2)
        )
        fallback = (
            self.eligible_tcp_endpoints is not None
            and self.eligible_tcp_endpoints <= MAX_FALLBACK_INVENTORY
            and self.sampled == self.eligible_tcp_endpoints
        )
        self.sufficient = (
            self.sampled is not None
            and self.sampled >= _MIN_SAMPLES_PER_CARRIER
            and self.tested == self.sampled
            and self.sampled_tcp_endpoints == self.sampled
            and self.skipped_udp_native_endpoints is not None
            and self.geographic_regions_sampled is not None
            and self.protocols_sampled is not None
            and self.sources_sampled is not None
            and self.strata_sampled is not None
            and self.eligible_tcp_endpoints is not None
            and self.outcomes is not None
            and (diverse or fallback)
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
        for name in (
            "sampled_tcp_endpoints",
            "skipped_udp_native_endpoints",
            "geographic_regions_sampled",
            "protocols_sampled",
            "sources_sampled",
            "strata_sampled",
            "eligible_tcp_endpoints",
            "outcomes",
        ):
            value = getattr(self, name)
            if value is not None:
                row[name] = value
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
                sampled_tcp_endpoints=row.get("sampled_tcp_endpoints"),
                skipped_udp_native_endpoints=row.get("skipped_udp_native_endpoints"),
                geographic_regions_sampled=row.get("geographic_regions_sampled"),
                protocols_sampled=row.get("protocols_sampled"),
                sources_sampled=row.get("sources_sampled"),
                strata_sampled=row.get("strata_sampled"),
                eligible_tcp_endpoints=row.get("eligible_tcp_endpoints"),
                outcomes=row.get("outcomes"),
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
    for identity_field in ("inventory_set_id", "probe_plan_id"):
        identity = payload.get(identity_field)
        if identity is not None and (
            not isinstance(identity, str) or _SAMPLE_SET_ID_PATTERN.fullmatch(identity) is None
        ):
            raise ValidationError(f"carrier qualification {identity_field} is invalid")
    sampler_version = payload.get("sampler_version")
    if sampler_version is not None and (
        not isinstance(sampler_version, int)
        or isinstance(sampler_version, bool)
        or sampler_version != SAMPLER_VERSION
    ):
        raise ValidationError("carrier qualification sampler version is invalid")
    plan_fields = (
        sample_set_id,
        payload.get("inventory_set_id"),
        payload.get("probe_plan_id"),
        sampler_version,
    )
    if any(value is not None for value in plan_fields[1:]) and any(
        value is None for value in plan_fields
    ):
        raise ValidationError("carrier qualification probe plan metadata is incomplete")
    # Producer-declared sufficiency must agree with the sample counts; drift
    # means the payload was hand-edited and fails closed.
    for carrier, row in carriers.items():
        claimed = row.get("sufficient_evidence")
        sampled = row.get("sampled")
        if claimed is None and sampled is None:
            continue  # legacy aggregate is accepted but insufficient
        verified = next(item for item in rows if item.carrier == carrier)
        expected = verified.sufficient
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
        for name in ("inventory_set_id", "probe_plan_id", "sampler_version"):
            if name in results:
                report[name] = results[name]
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
    for name in ("inventory_set_id", "probe_plan_id"):
        identity = value.get(name)
        if identity is not None and (
            not isinstance(identity, str) or _SAMPLE_SET_ID_PATTERN.fullmatch(identity) is None
        ):
            raise ValidationError(f"carrier qualification {name} is invalid")
    raw_sampler_version = value.get("sampler_version")
    if raw_sampler_version is not None and (
        not isinstance(raw_sampler_version, int)
        or isinstance(raw_sampler_version, bool)
        or raw_sampler_version != SAMPLER_VERSION
    ):
        raise ValidationError("carrier qualification sampler version is invalid")
    projected_plan_fields = (
        raw_sample_set_id,
        value.get("inventory_set_id"),
        value.get("probe_plan_id"),
        raw_sampler_version,
    )
    if any(item is not None for item in projected_plan_fields[1:]) and any(
        item is None for item in projected_plan_fields
    ):
        raise ValidationError("carrier qualification probe plan metadata is incomplete")
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
            sampled_tcp_endpoints=row.get("sampled_tcp_endpoints"),
            skipped_udp_native_endpoints=row.get("skipped_udp_native_endpoints"),
            geographic_regions_sampled=row.get("geographic_regions_sampled"),
            protocols_sampled=row.get("protocols_sampled"),
            sources_sampled=row.get("sources_sampled"),
            strata_sampled=row.get("strata_sampled"),
            eligible_tcp_endpoints=row.get("eligible_tcp_endpoints"),
            outcomes=row.get("outcomes"),
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
        for name in (
            "sampled_tcp_endpoints",
            "skipped_udp_native_endpoints",
            "geographic_regions_sampled",
            "protocols_sampled",
            "sources_sampled",
            "strata_sampled",
            "eligible_tcp_endpoints",
            "outcomes",
        ):
            measured = getattr(verified, name)
            if measured is not None:
                carrier_row[name] = measured
        carriers[carrier] = carrier_row
    safe: dict[str, Any] = {
        "status": status,
        "authority": _AUTHORITY,
        "carriers": carriers,
    }
    if raw_sample_set_id is not None:
        safe["sample_set_id"] = raw_sample_set_id
    for name in ("inventory_set_id", "probe_plan_id", "sampler_version"):
        if name in value:
            safe[name] = value[name]
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
