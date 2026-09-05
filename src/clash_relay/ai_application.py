"""In-process AI service qualification application service."""

from __future__ import annotations

import contextlib
import json
import tempfile
from pathlib import Path
from typing import Any

from .ai_qualification import AI_PROVIDER_PREFIX, load_ai_probe_specs, probe_ai_nodes
from .ai_qualification_cache import (
    ai_cache_summary,
    ai_runtime_fingerprints,
    cached_service_decisions,
    parse_ai_cache_bytes,
    update_ai_cache_service,
)
from .ai_service_qualification import rewrite_ai_service_qualified_candidate
from .errors import ValidationError
from .policy_document import load_policy_document
from .routing_policy_v2 import load_routing_policy_v2
from .scheduler_policy import load_scheduler_policy
from .service_qualification import (
    apply_service_route_postprocessing,
    service_qualification_by_probe,
    service_qualifications,
)
from .util import dump_yaml, load_yaml_file


def _service_diagnostics() -> dict[str, object]:
    diagnostics: dict[str, object] = {
        "qualification_mode": "per-service",
        "tested_nodes": 0,
        "selector_failures": 0,
        "probes": {},
    }
    for service in service_qualifications():
        key = service.diagnostics_key()
        if key is not None:
            diagnostics[key] = {}
    return diagnostics


def _cache_inputs(
    *,
    cache: Path | None,
    cache_key: Path | None,
    next_cache: Path | None,
) -> tuple[dict[str, Any], bytes, str] | None:
    provided = (cache is not None, cache_key is not None, next_cache is not None)
    if any(provided) and not all(provided):
        raise ValidationError("AI qualification cache requires cache, cache_key, and next_cache")
    if not all(provided):
        return None
    assert cache is not None and cache_key is not None
    try:
        key_text = cache_key.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ValidationError("failed to read private AI cache fingerprint key") from exc
    if not key_text:
        return None
    try:
        key = bytes.fromhex(key_text)
    except ValueError as exc:
        raise ValidationError("private AI cache fingerprint key is invalid") from exc
    try:
        content = cache.read_bytes()
    except OSError:
        content = None
    document, status = parse_ai_cache_bytes(content)
    return document, key, status


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


def run_ai_qualification(
    *,
    candidate: Path,
    policies: Path,
    mihomo_bin: Path,
    workers: int = 12,
    cache: Path | None = None,
    cache_key: Path | None = None,
    next_cache: Path | None = None,
) -> dict[str, Any]:
    """Qualify registered AI services and rewrite one private candidate in place."""

    diagnostics = _service_diagnostics()
    scheduler_policy = load_scheduler_policy(policies)
    policies_document = load_policy_document(policies).document
    routing_policy = load_routing_policy_v2(policies_document)
    probes = load_ai_probe_specs(policies)
    candidate_config = load_yaml_file(candidate)
    if not isinstance(candidate_config, dict):
        raise ValidationError("candidate is not a YAML mapping")
    cache_inputs = _cache_inputs(cache=cache, cache_key=cache_key, next_cache=next_cache)
    fingerprints: dict[str, str] | None = None
    cache_document: dict[str, Any] | None = None
    next_cache_document: dict[str, Any] | None = None
    cache_status = "disabled"
    if cache_inputs is not None:
        cache_document, fingerprint_key, cache_status = cache_inputs
        fingerprints = ai_runtime_fingerprints(candidate_config, fingerprint_key)
        next_cache_document = cache_document
        diagnostics["tested_nodes"] = len(fingerprints)

    qualified_by_probe: dict[str, set[str]] = {}
    expected_candidate_nodes: int | None = len(fingerprints) if fingerprints is not None else None
    total_live = 0
    total_cache_pass = 0
    total_cache_fail = 0
    for probe in probes:
        name = str(probe["name"])
        service = service_qualification_by_probe(name)
        service_cache_key = service.cache_key()
        pass_ttl_seconds, failure_ttl_seconds = service.cache_ttls(scheduler_policy.ai_cache)
        cached_pass: set[str] = set()
        cached_fail: set[str] = set()
        live_names: set[str] | None = None
        if cache_document is not None and fingerprints is not None:
            cached_pass, cached_fail, live_names = cached_service_decisions(
                cache_document,
                fingerprints,
                service_cache_key,
                pass_ttl_seconds=pass_ttl_seconds,
                failure_ttl_seconds=failure_ttl_seconds,
            )

        qualification_probes = service.qualification_probes(probe)
        live_qualified, probe_diagnostics = _probe_names(
            binary=mihomo_bin,
            candidate=candidate,
            names=live_names,
            probes=qualification_probes,
            workers=workers,
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

        selector_failures = diagnostics["selector_failures"]
        if not isinstance(selector_failures, int) or isinstance(selector_failures, bool):
            raise ValidationError("AI selector failure diagnostics must be an integer")
        diagnostics["selector_failures"] = selector_failures + int(
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
        probe_summary["cache_pass_ttl_seconds"] = pass_ttl_seconds
        probe_summary["cache_failure_ttl_seconds"] = failure_ttl_seconds
        if len(qualification_probes) > 1:
            probe_summary["critical_endpoints"] = len(qualification_probes)

        supporting_diagnostics: dict[str, Any] = {}
        supporting_qualified: set[str] = set()
        supporting_probes = service.supporting_probes()
        if supporting_probes and live_qualified:
            supporting_qualified, supporting_diagnostics = _probe_names(
                binary=mihomo_bin,
                candidate=candidate,
                names=live_qualified,
                probes=supporting_probes,
                workers=workers,
            )
        extended = service.build_extended_diagnostics(
            live_tested=live_tested,
            live_qualified=live_qualified,
            qualification_diagnostics=probe_diagnostics,
            supporting_diagnostics=supporting_diagnostics,
            supporting_qualified=supporting_qualified,
        )
        diagnostics_key = service.diagnostics_key()
        if diagnostics_key is not None and extended is not None:
            diagnostics[diagnostics_key] = extended

        probes_diagnostics = diagnostics["probes"]
        assert isinstance(probes_diagnostics, dict)
        probes_diagnostics[name] = probe_summary
        total_live += live_tested
        total_cache_pass += len(cached_pass)
        total_cache_fail += len(cached_fail)

        if next_cache_document is not None and fingerprints is not None and live_names_for_cache:
            next_cache_document = update_ai_cache_service(
                next_cache_document,
                fingerprints,
                service_cache_key,
                checked_names=live_names_for_cache,
                passed_names=live_qualified,
            )
            cache_document = next_cache_document

    cache_report: dict[str, object] = {
        "status": cache_status,
        "pass_ttl_seconds": scheduler_policy.ai_cache.pass_ttl_seconds,
        "failure_ttl_seconds": scheduler_policy.ai_cache.failure_ttl_seconds,
        "openai_pass_ttl_seconds": scheduler_policy.ai_cache.openai_pass_ttl_seconds,
        "openai_failure_ttl_seconds": scheduler_policy.ai_cache.openai_failure_ttl_seconds,
        "live_service_probes": total_live,
        "cache_pass_hits": total_cache_pass,
        "cache_fail_hits": total_cache_fail,
        "records": 0,
        "service_records": 0,
    }
    for service in service_qualifications():
        cache_report.update(service.cache_metadata())
    if next_cache_document is not None and next_cache is not None:
        next_cache.parent.mkdir(parents=True, exist_ok=True)
        next_cache.write_text(
            json.dumps(
                next_cache_document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        cache_report.update(ai_cache_summary(next_cache_document))

    report = rewrite_ai_service_qualified_candidate(
        candidate,
        qualified_by_probe,
        preferred_regions=routing_policy.ai.preferred_regions,
    )
    service_postprocessing = apply_service_route_postprocessing(candidate)
    return {
        "status": "qualified",
        "diagnostics": diagnostics,
        "qualification_cache": cache_report,
        **service_postprocessing,
        **report,
    }
