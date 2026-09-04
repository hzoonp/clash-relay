"""In-process browsing and transport qualification application service."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .browsing_qualification import load_browsing_probe_spec, probe_browsing_nodes
from .browsing_regions import provider_region
from .browsing_runtime import (
    apply_browsing_history_preference,
    rewrite_hardened_browsing_qualified_candidate,
)
from .errors import ClashRelayError, ValidationError
from .qualification_reliability import (
    QualificationStageRejected,
    classify_browsing_stage_failure,
)
from .scheduler_history import (
    browsing_runtime_names,
    history_summary,
    parse_history_bytes,
    preferred_stable_names,
    update_history,
)
from .scheduler_policy import load_scheduler_policy, preferred_stable_names_from_policy
from .transport_qualification import (
    probe_transport_nodes,
    rewrite_transport_qualified_candidate,
)
from .util import load_yaml_file

_MIN_PREFERRED_STABLE_NODES = 3


def _history_inputs(
    *,
    history: Path | None,
    history_key: Path | None,
    next_history: Path | None,
) -> tuple[dict[str, Any], bytes, str] | None:
    provided = (history is not None, history_key is not None, next_history is not None)
    if any(provided) and not all(provided):
        raise ValidationError(
            "browsing scheduler history requires history, history_key, and next_history together"
        )
    if not all(provided):
        return None
    assert history is not None and history_key is not None
    try:
        key_text = history_key.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ValidationError("failed to read private scheduler fingerprint key") from exc
    if not key_text:
        return None
    try:
        fingerprint_key = bytes.fromhex(key_text)
    except ValueError as exc:
        raise ValidationError("private scheduler fingerprint key is invalid") from exc
    try:
        content = history.read_bytes()
    except OSError:
        content = None
    history_document, status = parse_history_bytes(content)
    return history_document, fingerprint_key, status


def _cohort_latency(diagnostics: dict[str, object]) -> float | None:
    latency = diagnostics.get("qualified_latency_ms")
    if not isinstance(latency, dict):
        return None
    value = latency.get("p50")
    if isinstance(value, (int, float)) and value >= 0:
        return float(value)
    return None


def _runtime_names_by_region(candidate: dict[str, Any]) -> dict[str, set[str]]:
    providers = candidate.get("proxy-providers")
    if not isinstance(providers, dict):
        raise ValidationError("browsing regional qualification requires proxy-providers")
    result: dict[str, set[str]] = {}
    for provider_name, provider in providers.items():
        region = provider_region(str(provider_name))
        if region is None:
            continue
        payload = provider.get("payload") if isinstance(provider, dict) else None
        if not isinstance(payload, list):
            raise ValidationError("browsing regional qualification found an invalid provider")
        names = {
            str(proxy["name"])
            for proxy in payload
            if isinstance(proxy, dict) and isinstance(proxy.get("name"), str)
        }
        if names:
            result[region] = names
    if not result:
        raise ValidationError("browsing regional qualification found no regional inventory")
    return result


def _regional_history_summary(
    names_by_region: dict[str, set[str]],
    *,
    qualified: set[str],
    stable: set[str],
    preferred: set[str],
) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for region, names in sorted(names_by_region.items()):
        qualified_region = qualified & names
        stable_region = stable & names
        preferred_region = preferred & stable_region
        result[region] = {
            "tested": len(names),
            "qualified": len(qualified_region),
            "stable": len(stable_region),
            "preferred_stable": len(preferred_region),
            "historically_demoted": len(stable_region - preferred_region),
        }
    return result


def _apply_history_counts(
    report: dict[str, object],
    *,
    names_by_region: dict[str, set[str]],
    qualified: set[str],
    stable: set[str],
    preferred: set[str],
) -> None:
    regions = report.get("regions")
    if not isinstance(regions, dict):
        return
    stable_auto = 0
    reserve_auto = 0
    for region, names in names_by_region.items():
        region_report = regions.get(region)
        if not isinstance(region_report, dict):
            continue
        qualified_region = qualified & names
        stable_region = stable & qualified_region
        preferred_region = preferred & stable_region
        if len(preferred_region) >= _MIN_PREFERRED_STABLE_NODES:
            effective_stable = preferred_region
        else:
            effective_stable = stable_region or qualified_region
        reserve_region = qualified_region - effective_stable
        if qualified_region and not reserve_region:
            reserve_region = set(qualified_region)
        region_report["stable_automatic"] = len(effective_stable)
        region_report["reserve_automatic"] = len(reserve_region)
        stable_auto += len(effective_stable)
        reserve_auto += len(reserve_region)
    report["stable_automatic_nodes"] = stable_auto
    report["reserve_automatic_nodes"] = reserve_auto


def run_browsing_qualification(
    *,
    candidate: Path,
    policies: Path,
    mihomo_bin: Path,
    workers: int = 12,
    attempts: int = 3,
    required_successes: int = 2,
    history: Path | None = None,
    history_key: Path | None = None,
    next_history: Path | None = None,
) -> dict[str, Any]:
    """Qualify browsing/transport nodes and rewrite one private candidate in place."""

    diagnostics: dict[str, object] = {}
    transport_diagnostics: dict[str, object] = {}
    failure_stage = "setup"
    try:
        scheduler_policy = load_scheduler_policy(policies)
        attempts = scheduler_policy.browsing.attempts if scheduler_policy.declared else attempts
        required_successes = (
            scheduler_policy.browsing.reserve_successes
            if scheduler_policy.declared
            else required_successes
        )
        candidate_before = load_yaml_file(candidate)
        if not isinstance(candidate_before, dict):
            raise ValidationError("candidate is not a YAML mapping")
        all_names = browsing_runtime_names(candidate_before)
        names_by_region = _runtime_names_by_region(candidate_before)
        probe = load_browsing_probe_spec(policies)

        failure_stage = "browsing"
        qualified, stable = probe_browsing_nodes(
            mihomo_bin,
            candidate,
            probe,
            workers=workers,
            attempts=attempts,
            required_successes=required_successes,
            diagnostics=diagnostics,
        )

        failure_stage = "history"
        history_inputs = _history_inputs(
            history=history,
            history_key=history_key,
            next_history=next_history,
        )
        scheduler_report: dict[str, object] = {
            "status": "disabled",
            "state_version": 3,
            "records_before": 0,
            "records_after": 0,
            "stable_nodes": len(stable),
            "preferred_stable_nodes": len(stable),
            "historically_demoted_nodes": 0,
            "cohort_latency_ema_ms": None,
            "cohort_runs": 0,
            "preference_groups": 0,
            "regions": _regional_history_summary(
                names_by_region,
                qualified=qualified,
                stable=stable,
                preferred=stable,
            ),
        }
        preferred = set(stable)
        if history_inputs is not None:
            history_document, fingerprint_key, load_status = history_inputs
            if scheduler_policy.declared:
                preferred = preferred_stable_names_from_policy(
                    stable,
                    history_document,
                    fingerprint_key,
                    scheduler_policy.history,
                    now_epoch=int(time.time()),
                )
            else:
                preferred = preferred_stable_names(stable, history_document, fingerprint_key)
            next_history_document = update_history(
                history_document,
                all_names=all_names,
                qualified_names=qualified,
                stable_names=stable,
                preferred_names=preferred,
                fingerprint_key=fingerprint_key,
                cohort_latency_ms=_cohort_latency(diagnostics),
            )
            assert next_history is not None
            next_history.parent.mkdir(parents=True, exist_ok=True)
            next_history.write_text(
                json.dumps(
                    next_history_document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            scheduler_report = history_summary(
                load_status=load_status,
                before=history_document,
                after=next_history_document,
                stable_names=stable,
                preferred_names=preferred,
            )
            scheduler_report["preference_groups"] = 0
            scheduler_report["regions"] = _regional_history_summary(
                names_by_region,
                qualified=qualified,
                stable=stable,
                preferred=preferred,
            )

        failure_stage = "browsing_rewrite"
        report = rewrite_hardened_browsing_qualified_candidate(candidate, qualified, stable)
        if history_inputs is not None:
            preference_groups = apply_browsing_history_preference(
                candidate,
                preferred_names=preferred,
                stable_names=stable,
                qualified_names=qualified,
            )
            scheduler_report["preference_groups"] = preference_groups
            if preference_groups:
                _apply_history_counts(
                    report,
                    names_by_region=names_by_region,
                    qualified=qualified,
                    stable=stable,
                    preferred=preferred,
                )

        failure_stage = "transport"
        tcp_qualified, udp_qualified, _ = probe_transport_nodes(
            mihomo_bin,
            candidate,
            diagnostics=transport_diagnostics,
        )
        transport_report = rewrite_transport_qualified_candidate(
            candidate,
            tcp_qualified,
            udp_qualified,
        )

        return {
            "status": "qualified",
            "diagnostics": diagnostics,
            "scheduler_policy": {
                "declared": scheduler_policy.declared,
                "attempts": attempts,
                "reserve_successes": required_successes,
                "region_switch_interval": scheduler_policy.browsing.region_switch_interval,
                "history_min_runs": scheduler_policy.history.min_runs,
                "history_min_success_ema": scheduler_policy.history.min_success_ema,
                "history_recover_success_ema": scheduler_policy.history.recover_success_ema,
                "history_demote_after_failures": scheduler_policy.history.demote_after_failures,
            },
            "scheduler_history": scheduler_report,
            "transport_qualification": {
                "diagnostics": transport_diagnostics,
                **transport_report,
            },
            **report,
        }
    except ClashRelayError as exc:
        failure = classify_browsing_stage_failure(
            stage=failure_stage,
            message=str(exc),
            diagnostics=diagnostics,
        )
        raise QualificationStageRejected(
            stage=failure.stage,
            category=failure.category,
            retryable=failure.retryable,
            diagnostics=diagnostics,
            transport_diagnostics=transport_diagnostics,
        ) from exc
