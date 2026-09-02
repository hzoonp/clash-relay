from __future__ import annotations

import argparse
import contextlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from clash_relay.ai_qualification import AI_PROVIDER_PREFIX, load_ai_probe_specs, probe_ai_nodes
from clash_relay.ai_qualification_cache import (
    ai_cache_summary,
    ai_runtime_fingerprints,
    cached_service_decisions,
    parse_ai_cache_bytes,
    update_ai_cache_service,
)
from clash_relay.ai_service_qualification import rewrite_ai_service_qualified_candidate
from clash_relay.errors import ClashRelayError, ValidationError
from clash_relay.openai_app_contract import (
    cache_service_key,
    rewrite_route_locked_candidate,
)
from clash_relay.openai_app_contract import (
    contract_summary as openai_app_contract_summary,
)
from clash_relay.openai_app_contract import (
    critical_probes as openai_app_critical_probes,
)
from clash_relay.openai_app_contract import (
    supporting_probes as openai_app_supporting_probes,
)
from clash_relay.routing_policy_v2 import load_routing_policy_v2
from clash_relay.scheduler_policy import load_scheduler_policy
from clash_relay.util import dump_yaml, load_yaml_file


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Privately qualify generated AI nodes before production publication."
    )
    parser.add_argument("--candidate", type=_path, required=True)
    parser.add_argument("--policies", type=_path, required=True)
    parser.add_argument("--mihomo-bin", type=_path, required=True)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--cache", type=_path)
    parser.add_argument("--cache-key", type=_path)
    parser.add_argument("--next-cache", type=_path)
    return parser


def _service_diagnostics() -> dict[str, object]:
    return {
        "qualification_mode": "per-service",
        "tested_nodes": 0,
        "selector_failures": 0,
        "probes": {},
        "openai_app": {
            "contract": openai_app_contract_summary(),
            "critical": {},
            "supporting": {},
        },
    }


def _cache_inputs(args: argparse.Namespace) -> tuple[dict, bytes, str] | None:
    provided = (args.cache is not None, args.cache_key is not None, args.next_cache is not None)
    if any(provided) and not all(provided):
        raise ValidationError(
            "AI qualification cache requires --cache, --cache-key, and --next-cache"
        )
    if not all(provided):
        return None
    try:
        key_text = args.cache_key.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ValidationError("failed to read private AI cache fingerprint key") from exc
    if not key_text:
        return None
    try:
        key = bytes.fromhex(key_text)
    except ValueError as exc:
        raise ValidationError("private AI cache fingerprint key is invalid") from exc
    try:
        content = args.cache.read_bytes()
    except OSError:
        content = None
    cache, status = parse_ai_cache_bytes(content)
    return cache, key, status


def _filtered_candidate(candidate: Path, live_names: set[str]) -> Path:
    config = load_yaml_file(candidate)
    if not isinstance(config, dict):
        raise ValidationError("candidate is not a YAML mapping")
    providers = config.get("proxy-providers")
    if not isinstance(providers, dict):
        raise ValidationError("candidate proxy-providers must be a mapping")
    for provider_name, provider in providers.items():
        if not str(provider_name).startswith(AI_PROVIDER_PREFIX):
            continue
        payload = provider.get("payload") if isinstance(provider, dict) else None
        if not isinstance(payload, list):
            raise ValidationError("AI qualification provider payload is invalid")
        provider["payload"] = [
            proxy
            for proxy in payload
            if isinstance(proxy, dict) and str(proxy.get("name", "")) in live_names
        ]
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".yaml",
        prefix="ai-live-",
        dir=candidate.parent,
        delete=False,
    ) as handle:
        handle.write(dump_yaml(config))
        return Path(handle.name)


def _empty_probe_summary(probe: dict[str, object]) -> dict[str, object]:
    return {
        "method": str(probe["method"]),
        "expected_status": str(probe["expected_status"]),
        "passed": 0,
        "failed": 0,
        "outcomes": {},
    }


def _probe_names(
    *,
    binary: Path,
    candidate: Path,
    names: set[str] | None,
    probes: tuple[dict[str, Any], ...],
    workers: int,
) -> tuple[set[str], dict[str, Any]]:
    diagnostics: dict[str, Any] = {}
    temporary: Path | None = None
    try:
        target = candidate
        if names is not None:
            if not names:
                return set(), diagnostics
            temporary = _filtered_candidate(candidate, names)
            target = temporary
        qualified = probe_ai_nodes(
            binary,
            target,
            probes,
            workers=workers,
            diagnostics=diagnostics,
        )
        return qualified, diagnostics
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.unlink()


def _network_failure_count(probes: dict[str, Any], outcome: str) -> int:
    total = 0
    for summary in probes.values():
        if not isinstance(summary, dict):
            continue
        outcomes = summary.get("outcomes")
        if isinstance(outcomes, dict):
            total += int(outcomes.get(outcome, 0))
    return total


def _openai_app_diagnostics(
    *,
    live_tested: int,
    qualified: set[str],
    critical_diagnostics: dict[str, Any],
    supporting_diagnostics: dict[str, Any],
    supporting_fully_reachable: int,
) -> dict[str, Any]:
    critical = critical_diagnostics.get("probes", {})
    if not isinstance(critical, dict):
        critical = {}
    supporting = supporting_diagnostics.get("probes", {})
    if not isinstance(supporting, dict):
        supporting = {}
    return {
        "contract": openai_app_contract_summary(),
        "critical": {
            "live_tested_nodes": live_tested,
            "app_ready_live_nodes": len(qualified),
            "endpoint_count": len(critical),
            "tls_errors": _network_failure_count(critical, "tls_error"),
            "dns_errors": _network_failure_count(critical, "dns_error"),
            "timeouts": _network_failure_count(critical, "timeout"),
            "probes": critical,
        },
        "supporting": {
            "live_tested_nodes": int(supporting_diagnostics.get("tested_nodes", 0)),
            "fully_reachable_nodes": supporting_fully_reachable,
            "endpoint_count": len(supporting),
            "tls_errors": _network_failure_count(supporting, "tls_error"),
            "dns_errors": _network_failure_count(supporting, "dns_error"),
            "timeouts": _network_failure_count(supporting, "timeout"),
            "probes": supporting,
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    diagnostics = _service_diagnostics()
    try:
        scheduler_policy = load_scheduler_policy(args.policies)
        policies_document = load_yaml_file(args.policies)
        if not isinstance(policies_document, dict):
            raise ValidationError("policies document must be a YAML mapping")
        routing_policy = load_routing_policy_v2(policies_document)
        probes = load_ai_probe_specs(args.policies)
        candidate_config = load_yaml_file(args.candidate)
        if not isinstance(candidate_config, dict):
            raise ValidationError("candidate is not a YAML mapping")
        cache_inputs = _cache_inputs(args)
        fingerprints: dict[str, str] | None = None
        cache: dict | None = None
        next_cache: dict | None = None
        cache_status = "disabled"
        if cache_inputs is not None:
            cache, fingerprint_key, cache_status = cache_inputs
            fingerprints = ai_runtime_fingerprints(candidate_config, fingerprint_key)
            next_cache = cache
            diagnostics["tested_nodes"] = len(fingerprints)

        qualified_by_probe: dict[str, set[str]] = {}
        expected_candidate_nodes: int | None = (
            len(fingerprints) if fingerprints is not None else None
        )
        total_live = 0
        total_cache_pass = 0
        total_cache_fail = 0
        for probe in probes:
            name = str(probe["name"])
            service_cache_key = cache_service_key(name)
            cached_pass: set[str] = set()
            cached_fail: set[str] = set()
            live_names: set[str] | None = None
            if cache is not None and fingerprints is not None:
                cached_pass, cached_fail, live_names = cached_service_decisions(
                    cache,
                    fingerprints,
                    service_cache_key,
                    pass_ttl_seconds=scheduler_policy.ai_cache.pass_ttl_seconds,
                    failure_ttl_seconds=scheduler_policy.ai_cache.failure_ttl_seconds,
                )

            qualification_probes = (
                openai_app_critical_probes(probe) if name == "ai_openai" else (probe,)
            )
            live_qualified, probe_diagnostics = _probe_names(
                binary=args.mihomo_bin,
                candidate=args.candidate,
                names=live_names,
                probes=qualification_probes,
                workers=args.workers,
            )

            if live_names is None:
                live_tested = int(probe_diagnostics.get("tested_nodes", 0))
                if expected_candidate_nodes is None:
                    expected_candidate_nodes = live_tested
                    diagnostics["tested_nodes"] = live_tested
                elif live_tested != expected_candidate_nodes:
                    raise ValidationError("AI service probes tested inconsistent node inventories")
                live_names_for_cache: set[str] = set()
            else:
                live_tested = len(live_names)
                live_names_for_cache = live_names
            qualified = cached_pass | live_qualified
            qualified_by_probe[name] = qualified

            diagnostics["selector_failures"] = int(diagnostics["selector_failures"]) + int(
                probe_diagnostics.get("selector_failures", 0)
            )
            raw_probe_summaries = probe_diagnostics.get("probes", {})
            if not isinstance(raw_probe_summaries, dict):
                raw_probe_summaries = {}
            primary_summary = raw_probe_summaries.get(name)
            if isinstance(primary_summary, dict):
                probe_summary = dict(primary_summary)
            else:
                probe_summary = _empty_probe_summary(probe)
            probe_summary["live_tested_nodes"] = live_tested
            probe_summary["cache_pass_hits"] = len(cached_pass)
            probe_summary["cache_fail_hits"] = len(cached_fail)
            probe_summary["qualified_nodes"] = len(qualified)
            if name == "ai_openai":
                probe_summary["critical_endpoints"] = len(qualification_probes)
                supporting_diagnostics: dict[str, Any] = {}
                supporting_qualified: set[str] = set()
                if live_qualified:
                    supporting_qualified, supporting_diagnostics = _probe_names(
                        binary=args.mihomo_bin,
                        candidate=args.candidate,
                        names=live_qualified,
                        probes=openai_app_supporting_probes(),
                        workers=args.workers,
                    )
                diagnostics["openai_app"] = _openai_app_diagnostics(
                    live_tested=live_tested,
                    qualified=live_qualified,
                    critical_diagnostics=probe_diagnostics,
                    supporting_diagnostics=supporting_diagnostics,
                    supporting_fully_reachable=len(supporting_qualified),
                )
            diagnostics["probes"][name] = probe_summary
            total_live += live_tested
            total_cache_pass += len(cached_pass)
            total_cache_fail += len(cached_fail)

            if next_cache is not None and fingerprints is not None and live_names_for_cache:
                next_cache = update_ai_cache_service(
                    next_cache,
                    fingerprints,
                    service_cache_key,
                    checked_names=live_names_for_cache,
                    passed_names=live_qualified,
                )
                cache = next_cache

        cache_report: dict[str, object] = {
            "status": cache_status,
            "pass_ttl_seconds": scheduler_policy.ai_cache.pass_ttl_seconds,
            "failure_ttl_seconds": scheduler_policy.ai_cache.failure_ttl_seconds,
            "live_service_probes": total_live,
            "cache_pass_hits": total_cache_pass,
            "cache_fail_hits": total_cache_fail,
            "records": 0,
            "service_records": 0,
            "openai_contract_fingerprint": openai_app_contract_summary()["fingerprint"],
        }
        if next_cache is not None and args.next_cache is not None:
            args.next_cache.parent.mkdir(parents=True, exist_ok=True)
            args.next_cache.write_text(
                json.dumps(
                    next_cache,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            cache_report.update(ai_cache_summary(next_cache))

        report = rewrite_ai_service_qualified_candidate(
            args.candidate,
            qualified_by_probe,
            preferred_regions=routing_policy.ai.preferred_regions,
        )
        route_lock = rewrite_route_locked_candidate(args.candidate)
        print(
            json.dumps(
                {
                    "status": "qualified",
                    "diagnostics": diagnostics,
                    "qualification_cache": cache_report,
                    "openai_app_route_lock": route_lock,
                    **report,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except ClashRelayError as exc:
        if diagnostics["probes"]:
            print(
                json.dumps(
                    {"status": "rejected", "diagnostics": diagnostics},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
