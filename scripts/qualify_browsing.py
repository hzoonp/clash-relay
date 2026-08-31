from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from clash_relay.browsing_qualification import load_browsing_probe_spec, probe_browsing_nodes
from clash_relay.browsing_regions import provider_region
from clash_relay.browsing_runtime import (
    apply_browsing_history_preference,
    rewrite_hardened_browsing_qualified_candidate,
)
from clash_relay.errors import ClashRelayError, ValidationError
from clash_relay.scheduler_history import (
    browsing_runtime_names,
    history_summary,
    parse_history_bytes,
    preferred_stable_names,
    update_history,
)
from clash_relay.scheduler_policy import (
    load_scheduler_policy,
    preferred_stable_names_from_policy,
)
from clash_relay.util import load_yaml_file

_MIN_PREFERRED_STABLE_NODES = 3


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Privately qualify generated browsing nodes before production publication."
    )
    parser.add_argument("--candidate", type=_path, required=True)
    parser.add_argument("--policies", type=_path, required=True)
    parser.add_argument("--mihomo-bin", type=_path, required=True)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--required-successes", type=int, default=2)
    parser.add_argument("--history", type=_path)
    parser.add_argument("--history-key", type=_path)
    parser.add_argument("--next-history", type=_path)
    return parser


def _emit_safe_core_diagnostic(args: argparse.Namespace) -> None:
    script = Path(__file__).with_name("diagnose_browsing_core.py")
    try:
        subprocess.run(
            [
                sys.executable,
                str(script),
                "--candidate",
                str(args.candidate),
                "--mihomo-bin",
                str(args.mihomo_bin),
            ],
            check=False,
            timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired):
        print('{"status":"unavailable","reason":"diagnostic_process_failed"}', file=sys.stderr)


def _history_inputs(args: argparse.Namespace) -> tuple[dict, bytes, str] | None:
    provided = (
        args.history is not None,
        args.history_key is not None,
        args.next_history is not None,
    )
    if any(provided) and not all(provided):
        raise ValidationError(
            "browsing scheduler history requires --history, --history-key, and --next-history together"
        )
    if not all(provided):
        return None
    try:
        key_text = args.history_key.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ValidationError("failed to read private scheduler fingerprint key") from exc
    if not key_text:
        return None
    try:
        fingerprint_key = bytes.fromhex(key_text)
    except ValueError as exc:
        raise ValidationError("private scheduler fingerprint key is invalid") from exc
    try:
        content = args.history.read_bytes()
    except OSError:
        content = None
    history, status = parse_history_bytes(content)
    return history, fingerprint_key, status


def _cohort_latency(diagnostics: dict[str, object]) -> float | None:
    latency = diagnostics.get("qualified_latency_ms")
    if not isinstance(latency, dict):
        return None
    value = latency.get("p50")
    if isinstance(value, (int, float)) and value >= 0:
        return float(value)
    return None


def _runtime_names_by_region(candidate: dict) -> dict[str, set[str]]:
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    diagnostics: dict[str, object] = {}
    try:
        scheduler_policy = load_scheduler_policy(args.policies)
        attempts = (
            scheduler_policy.browsing.attempts if scheduler_policy.declared else args.attempts
        )
        required_successes = (
            scheduler_policy.browsing.reserve_successes
            if scheduler_policy.declared
            else args.required_successes
        )
        candidate_before = load_yaml_file(args.candidate)
        if not isinstance(candidate_before, dict):
            raise ValidationError("candidate is not a YAML mapping")
        all_names = browsing_runtime_names(candidate_before)
        names_by_region = _runtime_names_by_region(candidate_before)
        probe = load_browsing_probe_spec(args.policies)
        qualified, stable = probe_browsing_nodes(
            args.mihomo_bin,
            args.candidate,
            probe,
            workers=args.workers,
            attempts=attempts,
            required_successes=required_successes,
            diagnostics=diagnostics,
        )

        history_inputs = _history_inputs(args)
        scheduler_report: dict[str, object] = {
            "status": "disabled",
            "state_version": 2,
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
            history, fingerprint_key, load_status = history_inputs
            if scheduler_policy.declared:
                preferred = preferred_stable_names_from_policy(
                    stable,
                    history,
                    fingerprint_key,
                    scheduler_policy.history,
                    now_epoch=int(time.time()),
                )
            else:
                preferred = preferred_stable_names(stable, history, fingerprint_key)
            next_history = update_history(
                history,
                all_names=all_names,
                qualified_names=qualified,
                stable_names=stable,
                fingerprint_key=fingerprint_key,
                cohort_latency_ms=_cohort_latency(diagnostics),
            )
            args.next_history.parent.mkdir(parents=True, exist_ok=True)
            args.next_history.write_text(
                json.dumps(
                    next_history,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            scheduler_report = history_summary(
                load_status=load_status,
                before=history,
                after=next_history,
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

        report = rewrite_hardened_browsing_qualified_candidate(
            args.candidate,
            qualified,
            stable,
        )
        if history_inputs is not None:
            preference_groups = apply_browsing_history_preference(
                args.candidate,
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

        print(
            json.dumps(
                {
                    "status": "qualified",
                    "diagnostics": diagnostics,
                    "scheduler_policy": {
                        "declared": scheduler_policy.declared,
                        "attempts": attempts,
                        "reserve_successes": required_successes,
                        "region_switch_interval": scheduler_policy.browsing.region_switch_interval,
                    },
                    "scheduler_history": scheduler_report,
                    **report,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except ClashRelayError as exc:
        if diagnostics:
            print(
                json.dumps(
                    {"status": "rejected", "diagnostics": diagnostics},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        if "Mihomo rejected the browsing qualification configuration" in str(exc):
            _emit_safe_core_diagnostic(args)
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
