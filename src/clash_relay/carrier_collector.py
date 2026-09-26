"""Fail-closed merge of private self-hosted carrier aggregate rows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .carrier_qualification import (
    SAMPLER_VERSION,
    parse_carrier_aggregate_payload,
    run_carrier_qualification,
)
from .errors import ValidationError

MAX_TIMESTAMP_DRIFT_SECONDS = 300


def collect_carrier_probes(
    payloads: Sequence[Mapping[str, Any]], *, now_epoch: int | None = None
) -> dict[str, Any]:
    if len(payloads) != 3:
        raise ValidationError("carrier collector requires all three carrier aggregates")
    rows: dict[str, dict[str, Any]] = {}
    sample_sets: set[str] = set()
    inventory_sets: set[str] = set()
    probe_plans: set[str] = set()
    sampler_versions: set[int] = set()
    timestamps: list[int] = []
    sampled_counts: set[int] = set()
    for payload in payloads:
        parsed, collected, sample_set_id = parse_carrier_aggregate_payload(payload)
        if len(parsed) != 1 or sample_set_id is None:
            raise ValidationError(
                "carrier collector requires one row and one sample set per producer"
            )
        inventory_set_id = payload.get("inventory_set_id")
        probe_plan_id = payload.get("probe_plan_id")
        sampler_version = payload.get("sampler_version")
        if (
            not isinstance(inventory_set_id, str)
            or not isinstance(probe_plan_id, str)
            or sampler_version != SAMPLER_VERSION
            or parsed[0].sampled_tcp_endpoints is None
            or parsed[0].skipped_udp_native_endpoints is None
            or parsed[0].strata_sampled is None
        ):
            raise ValidationError("carrier collector requires complete producer plan metadata")
        carrier = parsed[0].carrier
        if carrier in rows:
            raise ValidationError("carrier collector repeats a carrier")
        source_rows = payload["carriers"]
        rows[carrier] = dict(source_rows[carrier])
        sampled_counts.add(parsed[0].sampled if parsed[0].sampled is not None else -1)
        sample_sets.add(sample_set_id)
        inventory_sets.add(inventory_set_id)
        probe_plans.add(probe_plan_id)
        sampler_versions.add(sampler_version)
        timestamps.append(collected)
        # Each input must be current before it can participate in a merge.
        if run_carrier_qualification(payload, now_epoch=now_epoch)["evidence"]["status"] == "stale":
            raise ValidationError("carrier collector rejects stale evidence")
    if set(rows) != {"telecom", "unicom", "mobile"}:
        raise ValidationError("carrier collector requires telecom, unicom and mobile")
    if len(sample_sets) != 1:
        raise ValidationError("carrier collector sample sets differ")
    if len(inventory_sets) != 1:
        raise ValidationError("carrier collector inventories differ")
    if len(probe_plans) != 1:
        raise ValidationError("carrier collector probe plans differ")
    if len(sampler_versions) != 1 or next(iter(sampler_versions)) != SAMPLER_VERSION:
        raise ValidationError("carrier collector sampler versions differ")
    if len(sampled_counts) != 1:
        raise ValidationError("carrier collector sample counts differ")
    if max(timestamps) - min(timestamps) > MAX_TIMESTAMP_DRIFT_SECONDS:
        raise ValidationError("carrier collector timestamp drift exceeds bound")
    merged = {
        "schema_version": 1,
        "collected_at_epoch": min(timestamps),
        "sample_set_id": next(iter(sample_sets)),
        "inventory_set_id": next(iter(inventory_sets)),
        "probe_plan_id": next(iter(probe_plans)),
        "sampler_version": next(iter(sampler_versions)),
        "carriers": dict(sorted(rows.items())),
    }
    run_carrier_qualification(merged, now_epoch=now_epoch)
    return merged
