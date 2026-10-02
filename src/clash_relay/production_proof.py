"""Build a privacy-safe proof for a validated production candidate."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .qualification_observability import (
    render_qualification_observability_markdown,
    safe_qualification_observability,
)
from .runtime_graph import RuntimeGraph
from .util import contains_yaml_incompatible_control_characters, load_yaml_file
from .web_general_runtime import WEB_GENERAL_AUTO_GROUP

_ALLOWED_PUBLICATION_STATUSES = frozenset({"dry-run", "preflight", "published"})
_RELEASE_ID = re.compile(r"^[0-9a-f]{64}$")
_QUALIFICATION_TIMINGS = frozenset({"browsing_transport", "ai", "service_client_path", "total"})


def _json_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"production proof requires a {label} mapping")
    return value


def _safe_qualification(
    value: dict[str, Any] | None, *, known_source_ids: list[str]
) -> dict[str, Any] | None:
    if value is None:
        return None
    if value.get("status") != "qualified":
        raise ValidationError(
            "production proof requires a successful unified qualification pipeline"
        )
    result: dict[str, Any] = {"status": "qualified"}
    stages = value.get("stages")
    result["stages"] = len(stages) if isinstance(stages, list) else 0
    timings = value.get("timings_ms")
    if isinstance(timings, dict):
        safe: dict[str, float] = {}
        for name, duration in sorted(timings.items()):
            if (
                name in _QUALIFICATION_TIMINGS
                and isinstance(duration, (int, float))
                and not isinstance(duration, bool)
                and 0 <= float(duration) <= 24 * 60 * 60 * 1000
            ):
                safe[name] = round(float(duration), 3)
        if safe:
            result["timings_ms"] = safe
    result.update(safe_qualification_observability(value, known_source_ids=known_source_ids))
    return result


def _safe_release(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    release_id = value.get("release_id")
    if value.get("status") not in {"published", "unchanged"} or not isinstance(release_id, str):
        raise ValidationError("production proof received invalid release metadata")
    if _RELEASE_ID.fullmatch(release_id) is None:
        raise ValidationError("production proof received an invalid release id")
    return {
        "status": value["status"],
        "release_id": release_id,
        "production_changed": value.get("production_changed") is True,
    }


def _safe_count(value: dict[str, Any], key: str) -> int:
    raw = value.get(key, 0)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0
    return max(0, int(raw))


def _safe_web_general(audit: Any, qualification: Any, *, required: bool) -> dict[str, Any]:
    if not required:
        return {"status": "not_applicable"}
    if (
        not isinstance(audit, dict)
        or audit.get("status") != "passed"
        or audit.get("source_use") != "general"
        or audit.get("subscription_1_reachable") is not False
        or audit.get("regional_scheduler") != "passed"
        or not isinstance(qualification, dict)
        or qualification.get("status") != "qualified"
        or qualification.get("source_use") != "general"
        or qualification.get("qualification") != "passed"
        or _safe_count(qualification, "qualified_nodes") == 0
    ):
        raise ValidationError(
            "production proof requires general web source, scheduler and qualification evidence"
        )
    return {
        "status": "passed",
        "source_use": "general",
        "subscription_1_reachable": False,
        "regional_scheduler": "passed",
        "qualification": "passed",
        "qualified_nodes": _safe_count(qualification, "qualified_nodes"),
    }


def _safe_download_routing(value: Any, *, required: bool) -> dict[str, Any]:
    if value is None or (isinstance(value, dict) and value.get("status") == "not_applicable"):
        if required:
            raise ValidationError("production proof requires download isolation evidence")
        return {
            "configuration_guarantee": "not_applicable",
            "deployment_guarantee": "unverified",
        }
    if not isinstance(value, dict) or value.get("status") != "passed":
        raise ValidationError("production proof requires a passed download isolation audit")
    if (
        value.get("runtime_graph_check") != "passed"
        or value.get("terminal_guards") != "passed"
        or value.get("download_listener_bound") is not True
        or value.get("subscription_1_reachable") is not False
    ):
        raise ValidationError("production proof found incomplete download isolation evidence")
    return {
        "configuration_guarantee": "passed",
        "deployment_guarantee": "unverified",
        "subscription_1_reachable": False,
        "inbound_configured": value.get("inbound_configured") is True,
        "download_listener_bound": True,
        "terminal_guards": "passed",
        "runtime_graph_check": "passed",
        "process_rules": int(value.get("process_rules", 0)),
        "domain_rules": int(value.get("domain_rules", 0)),
    }


def _safe_admission_reason_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for reason, count in sorted(value.items()):
        if (
            isinstance(reason, str)
            and re.fullmatch(r"[a-z0-9_]{1,64}", reason)
            and isinstance(count, int)
            and not isinstance(count, bool)
            and count >= 0
        ):
            result[reason] = count
    return result


def _safe_source_admission_rows(value: Any) -> tuple[dict[str, Any], int, dict[str, int]]:
    if not isinstance(value, list):
        return {}, 0, {}
    by_source: dict[str, Any] = {}
    total_skipped = 0
    reason_totals: dict[str, int] = {}
    for row in value:
        if not isinstance(row, dict):
            continue
        source_id = row.get("id")
        if (
            not isinstance(source_id, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", source_id) is None
        ):
            continue
        reasons = _safe_admission_reason_counts(row.get("skipped_invalid_reasons"))
        skipped = _safe_count(row, "skipped_invalid_nodes")
        total_skipped += skipped
        for reason, count in reasons.items():
            reason_totals[reason] = reason_totals.get(reason, 0) + count
        by_source[source_id] = {
            "input_nodes": _safe_count(row, "input_nodes"),
            "parsed_valid_nodes": _safe_count(row, "parsed_valid_nodes"),
            "skipped_invalid_nodes": skipped,
            "skipped_invalid_reasons": reasons,
        }
    return by_source, total_skipped, dict(sorted(reason_totals.items()))


def _safe_build_observability(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    subscriptions = value.get("subscriptions")
    configured = len(subscriptions) if isinstance(subscriptions, list) else 0
    by_source, skipped_invalid, skipped_reasons = _safe_source_admission_rows(subscriptions)

    compatibility = value.get("dns_compatibility_audit")
    leak = value.get("dns_leak_audit")
    runtime = value.get("dns_runtime_audit")
    dns: dict[str, Any] = {}

    if isinstance(compatibility, dict):
        status = compatibility.get("status")
        dns["compatibility"] = {
            "status": status if status in {"passed", "not_applicable"} else "unknown",
            "mode": compatibility.get("mode")
            if compatibility.get("mode") in {"fake_ip", "no_managed_dns", "not_fake_ip"}
            else "unknown",
            "entries": _safe_count(compatibility, "entries"),
            "compatibility_entries": _safe_count(compatibility, "compatibility_entries"),
        }
    if isinstance(leak, dict):
        status = leak.get("status")
        dns["leak"] = {
            "status": status if status in {"passed", "not_applicable"} else "unknown",
            "mode": leak.get("mode")
            if leak.get("mode") in {"strict_tun", "no_tun", "tun_disabled"}
            else "unknown",
            "policy_rulesets": _safe_count(leak, "policy_rulesets"),
            "exact_domain_overrides": _safe_count(leak, "exact_domain_overrides"),
            "encrypted_resolver_fields": _safe_count(leak, "encrypted_resolver_fields"),
            "dns_hijack_protocols": _safe_count(leak, "dns_hijack_protocols"),
        }
    if isinstance(runtime, dict):
        status = runtime.get("status")
        dns["runtime"] = {
            "status": status if status in {"passed", "not_applicable"} else "unknown",
            "resolver_transport": "independent"
            if runtime.get("resolver_transport") == "independent"
            else "unknown",
            "direct_resolver_policy": "bypass"
            if runtime.get("direct_resolver_policy") == "bypass"
            else "unknown",
            "automatic_groups": _safe_count(runtime, "automatic_groups"),
        }

    return {
        "source_admission": {
            "configured_subscriptions": configured,
            "successful_subscriptions": _safe_count(value, "successful_subscriptions"),
            "parsed_nodes": _safe_count(value, "parsed_nodes"),
            "usable_nodes": _safe_count(value, "usable_nodes"),
            "duplicates_removed": _safe_count(value, "duplicates_removed"),
            "informational_rejected": _safe_count(value, "informational_nodes_rejected"),
            "name_filtered": _safe_count(value, "name_filtered_nodes"),
            "multiplier_filtered": _safe_count(value, "multiplier_filtered_nodes"),
            "skipped_invalid_nodes": skipped_invalid,
            "skipped_invalid_reasons": skipped_reasons,
            "by_source": by_source,
        },
        "dns": dns,
    }


def _safe_endpoint_qualification(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    endpoint = value.get("endpoint_qualification")
    if not isinstance(endpoint, dict):
        return None
    status = endpoint.get("status")
    return {
        "status": status if status in {"passed", "skipped"} else "unknown",
        **{
            key: _safe_count(endpoint, key)
            for key in (
                "tcp_nodes",
                "tested",
                "reachable",
                "unreachable",
                "quarantined",
                "skipped_udp_native",
                "robust_endpoints",
                "reserve_endpoints",
                "dns_inconclusive",
                "timeout_reserve",
            )
        },
    }


def _safe_network_evidence(
    *,
    candidate: dict[str, Any],
    qualification: dict[str, Any] | None,
    build_report: dict[str, Any] | None,
) -> dict[str, str]:
    """Project independent authorities without copying private probe input."""
    declaration = build_report.get("network_profile") if isinstance(build_report, dict) else None
    profile = declaration.get("profile") if isinstance(declaration, dict) else declaration
    if profile not in {"default", "cn_three_net"}:
        profile = "unknown"
    reachability = qualification.get("reachability") if isinstance(qualification, dict) else None
    carrier = reachability.get("carrier_qualification") if isinstance(reachability, dict) else None
    target = "not_configured"
    if isinstance(carrier, dict):
        status = carrier.get("status")
        if status in {"passed", "stale", "not_configured"}:
            target = "observed" if status == "passed" else status
            if (
                status == "passed"
                and carrier.get("schema_version") == 2
                and carrier.get("profile") != profile
            ):
                target = "profile_mismatch"
    groups = candidate.get("proxy-groups")
    client = (
        "enabled"
        if isinstance(groups, list)
        and any(
            isinstance(group, dict)
            and group.get("type") == "url-test"
            and isinstance(group.get("url"), str)
            and group["url"]
            for group in groups
        )
        else "disabled"
    )
    return {
        "network_profile": profile,
        "static_eligible": "passed",
        "runner_qualification": "passed" if qualification is not None else "unavailable",
        "target_network_evidence": target,
        "client_urltest": client,
    }


def _safe_promotion_guard(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    status = value.get("status")
    if status not in {"passed", "blocked"}:
        raise ValidationError("production proof received invalid Promotion Guard status")
    admission_state = value.get("admission_state")
    if admission_state is not None and admission_state not in {
        "NORMAL",
        "WARNING",
        "PROMOTION_BLOCK",
    }:
        raise ValidationError("production proof received invalid Promotion Guard admission state")
    reason = value.get("reason")
    safe_reasons = {
        "disabled",
        "first_release",
        "within_thresholds",
        "degraded_warning",
        "degraded",
        "availability_contract",
        "probe_environment_hold",
    }
    warnings = value.get("warnings")
    violations = value.get("violations")
    result = {
        "status": status,
        "reason": reason if reason in safe_reasons else "other",
        "violations": len(violations) if isinstance(violations, list) else 0,
    }
    if admission_state is not None:
        result["admission_state"] = admission_state
    if isinstance(warnings, list):
        result["warnings"] = len(warnings)
    return result


def _safe_openai_app(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    critical = value.get("critical")
    supporting = value.get("supporting")
    if not isinstance(critical, dict) or not isinstance(supporting, dict):
        return None
    return {
        "app_ready_live_nodes": max(0, int(critical.get("app_ready_live_nodes", 0))),
        "critical_endpoints": max(0, int(critical.get("endpoint_count", 0))),
        "critical_tls_errors": max(0, int(critical.get("tls_errors", 0))),
        "critical_dns_errors": max(0, int(critical.get("dns_errors", 0))),
        "critical_timeouts": max(0, int(critical.get("timeouts", 0))),
        "supporting_endpoints": max(0, int(supporting.get("endpoint_count", 0))),
        "supporting_tls_errors": max(0, int(supporting.get("tls_errors", 0))),
    }


def _safe_openai_client_path(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    selection = value.get("selection")
    if status not in {"passed", "fail_closed"}:
        raise ValidationError("production proof received invalid OpenAI client-path status")
    if selection not in {"stable_first_fallback", "reject"}:
        raise ValidationError("production proof received invalid OpenAI client-path selection")
    return {
        "status": status,
        "selection": selection,
        "runtime_regions": max(0, int(value.get("runtime_regions", 0))),
        "runtime_providers": max(0, int(value.get("runtime_providers", 0))),
        "runtime_nodes": max(0, int(value.get("runtime_nodes", 0))),
    }


def build_production_proof(
    *,
    candidate_path: Path,
    audit: dict[str, Any],
    browsing: dict[str, Any],
    ai: dict[str, Any],
    validated_cores: tuple[str, ...],
    publication_status: str,
    qualification: dict[str, Any] | None = None,
    release: dict[str, Any] | None = None,
    build_report: dict[str, Any] | None = None,
    promotion_guard: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return aggregate-only metadata for the exact validated candidate bytes."""
    if publication_status not in _ALLOWED_PUBLICATION_STATUSES:
        raise ValidationError("production proof received an invalid publication status")
    if not validated_cores or len(set(validated_cores)) != len(validated_cores):
        raise ValidationError("production proof requires unique validated Mihomo core versions")
    try:
        content = candidate_path.read_bytes()
    except OSError as exc:
        raise ValidationError("production proof could not read the private candidate") from exc
    if not content:
        raise ValidationError("production proof refuses an empty candidate")
    candidate = load_yaml_file(candidate_path)
    if not isinstance(candidate, dict):
        raise ValidationError("production proof candidate must be a YAML mapping")
    if contains_yaml_incompatible_control_characters(candidate):
        raise ValidationError("production proof found FlClash-incompatible YAML control characters")

    audit = _json_mapping(audit, "post-qualification audit")
    reachability = _json_mapping(audit.get("reachability"), "reachability audit")
    if audit.get("status") != "passed" or reachability.get("status") != "passed":
        raise ValidationError("production proof requires a passed source reachability audit")

    browsing = _json_mapping(browsing, "browsing qualification")
    browsing_diagnostics = _json_mapping(
        browsing.get("diagnostics"), "browsing qualification diagnostics"
    )
    if browsing.get("status") != "qualified":
        raise ValidationError("production proof requires successful browsing qualification")

    ai = _json_mapping(ai, "AI qualification")
    ai_diagnostics = _json_mapping(ai.get("diagnostics"), "AI qualification diagnostics")
    service_counts = _json_mapping(ai.get("service_qualified_nodes"), "AI service counts")
    if ai.get("status") != "qualified":
        raise ValidationError("production proof requires successful AI qualification")

    ai_proof: dict[str, Any] = {
        "tested": int(ai_diagnostics.get("tested_nodes", 0)),
        "selector_failures": int(ai_diagnostics.get("selector_failures", 0)),
        "service_qualified": {
            str(name): int(count) for name, count in sorted(service_counts.items())
        },
        "service_fail_closed": sorted(str(name) for name in ai.get("service_fail_closed", [])),
    }
    openai_app = _safe_openai_app(ai_diagnostics.get("openai_app"))
    if openai_app is not None:
        ai_proof["openai_app"] = openai_app
    openai_client_path = _safe_openai_client_path(audit.get("openai_client_path"))
    if openai_client_path is not None:
        ai_proof["openai_client_path"] = openai_client_path

    web_general_required = any(
        isinstance(group, dict) and group.get("name") == WEB_GENERAL_AUTO_GROUP
        for group in candidate.get("proxy-groups", [])
    )
    web_general_proof = _safe_web_general(
        audit.get("web_general"), browsing.get("web_general"), required=web_general_required
    )
    if web_general_required and web_general_proof["qualified_nodes"] != len(
        RuntimeGraph.from_candidate(candidate).effective_leaf_proxies(WEB_GENERAL_AUTO_GROUP)
    ):
        raise ValidationError("general web qualification evidence does not match final runtime")

    proof: dict[str, Any] = {
        "status": "passed",
        "candidate": {
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "proxy_providers": len(candidate.get("proxy-providers", {})),
            "proxy_groups": len(candidate.get("proxy-groups", [])),
            "rule_providers": len(candidate.get("rule-providers", {})),
            "rules": len(candidate.get("rules", [])),
        },
        "source_reachability": {
            "status": "passed",
            "groups_checked": int(reachability.get("groups_checked", 0)),
            "routing_surfaces_checked": int(reachability.get("routing_surfaces_checked", 0)),
            "runtime_rules_checked": int(reachability.get("runtime_rules_checked", 0)),
        },
        "download_routing": _safe_download_routing(
            audit.get("download_routing"),
            required=any(
                isinstance(row, dict) and row.get("id") == "subscription_1"
                for row in audit.get("subscriptions", [])
            ),
        ),
        "browsing": {
            "tested": int(browsing_diagnostics.get("tested_nodes", 0)),
            "qualified": int(browsing.get("qualified_nodes", 0)),
            "stable": int(browsing.get("stable_nodes", 0)),
            "reserve": int(browsing.get("reserve_nodes", 0)),
            "rejected": int(browsing.get("failed_nodes", 0)),
            "automatic": int(browsing.get("automatic_nodes", 0)),
        },
        "web_general": web_general_proof,
        "ai": ai_proof,
        "validated_cores": list(validated_cores),
        "publication": publication_status,
        "client_compatibility": {"yaml_portability": "passed"},
        "network_evidence": _safe_network_evidence(
            candidate=candidate, qualification=qualification, build_report=build_report
        ),
    }
    safe_qualification = _safe_qualification(
        qualification,
        known_source_ids=[
            row["id"]
            for row in audit.get("subscriptions", [])
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        ],
    )
    if safe_qualification is not None:
        proof["qualification_pipeline"] = safe_qualification
    endpoint_qualification = _safe_endpoint_qualification(qualification)
    if endpoint_qualification is not None:
        proof["endpoint_qualification"] = endpoint_qualification

    build_observability = _safe_build_observability(build_report)
    if build_observability is not None:
        proof.update(build_observability)

    safe_promotion = _safe_promotion_guard(promotion_guard)
    if safe_promotion is not None:
        proof["promotion_guard"] = safe_promotion

    safe_release = _safe_release(release)
    if safe_release is not None:
        if safe_release["release_id"] != proof["candidate"]["sha256"]:
            raise ValidationError("production release id does not match the proved candidate bytes")
        proof["release"] = safe_release
    return proof


def render_production_proof_markdown(proof: dict[str, Any]) -> str:
    """Render the safe proof for GitHub Actions Summary."""
    candidate = _json_mapping(proof.get("candidate"), "candidate proof")
    reachability = _json_mapping(proof.get("source_reachability"), "reachability proof")
    browsing = _json_mapping(proof.get("browsing"), "browsing proof")
    ai = _json_mapping(proof.get("ai"), "AI proof")
    service_counts = _json_mapping(ai.get("service_qualified"), "AI service proof")
    cores = proof.get("validated_cores")
    if not isinstance(cores, list):
        raise ValidationError("production proof has invalid core metadata")

    lines = [
        "## Production proof",
        "",
        f"Publication: **{proof.get('publication')}**  ",
        f"Candidate: **{candidate['bytes']} bytes** · SHA-256 `{candidate['sha256']}`  ",
        f"Validated cores: **{', '.join(str(item) for item in cores)}**",
        "",
        "| Gate | Result |",
        "| --- | ---: |",
        f"| Source reachability | {reachability['status']} |",
        f"| Routing groups checked | {reachability['groups_checked']} |",
        f"| Routing surfaces checked | {reachability['routing_surfaces_checked']} |",
        f"| Runtime rules checked | {reachability['runtime_rules_checked']} |",
        f"| Browsing tested | {browsing['tested']} |",
        f"| Browsing qualified | {browsing['qualified']} |",
        f"| Browsing stable / reserve | {browsing['stable']} / {browsing['reserve']} |",
        f"| Browsing automatic | {browsing['automatic']} |",
        f"| Browsing rejected | {browsing['rejected']} |",
        f"| AI tested | {ai['tested']} |",
        f"| AI selector failures | {ai['selector_failures']} |",
    ]
    network_evidence = proof.get("network_evidence")
    web_general = proof.get("web_general")
    if isinstance(web_general, dict) and web_general.get("status") == "passed":
        lines.extend(
            [
                "| General web source isolation | passed |",
                "| General web regional scheduler | passed |",
                "| General web qualification | passed |",
                f"| General web qualified nodes | {web_general['qualified_nodes']} |",
            ]
        )
    if isinstance(network_evidence, dict):
        for label, key in (
            ("Network profile", "network_profile"),
            ("Static eligible", "static_eligible"),
            ("Runner qualification", "runner_qualification"),
            ("Target-network evidence", "target_network_evidence"),
            ("Client URLTest", "client_urltest"),
        ):
            lines.append(f"| {label} | {network_evidence.get(key, 'unknown')} |")
    for name, count in service_counts.items():
        lines.append(f"| AI {name} qualified | {count} |")
    openai_app = ai.get("openai_app")
    if isinstance(openai_app, dict):
        lines.extend(
            [
                f"| OpenAI App-ready live nodes | {openai_app['app_ready_live_nodes']} |",
                f"| OpenAI critical endpoints | {openai_app['critical_endpoints']} |",
                f"| OpenAI critical TLS / DNS / timeout failures | {openai_app['critical_tls_errors']} / {openai_app['critical_dns_errors']} / {openai_app['critical_timeouts']} |",
                f"| OpenAI supporting endpoints | {openai_app['supporting_endpoints']} |",
                f"| OpenAI supporting TLS failures | {openai_app['supporting_tls_errors']} |",
            ]
        )
    client_path = ai.get("openai_client_path")
    if isinstance(client_path, dict):
        lines.extend(
            [
                f"| OpenAI client-path | {client_path['status']} |",
                f"| OpenAI client-path selection | {client_path['selection']} |",
                f"| OpenAI client-path regions | {client_path['runtime_regions']} |",
                f"| OpenAI client-path providers | {client_path['runtime_providers']} |",
                f"| OpenAI client-path nodes | {client_path['runtime_nodes']} |",
            ]
        )
    fail_closed = ai.get("service_fail_closed", [])
    lines.append(
        f"| AI service fail-closed | {', '.join(fail_closed) if fail_closed else 'none'} |"
    )

    client_compatibility = proof.get("client_compatibility")
    if isinstance(client_compatibility, dict):
        portability = client_compatibility.get("yaml_portability", "unknown")
        lines.append(f"| FlClash YAML portability | {portability} |")

    source_admission = proof.get("source_admission")
    if isinstance(source_admission, dict):
        lines.extend(
            [
                f"| Subscriptions successful / configured | {source_admission.get('successful_subscriptions', 0)} / {source_admission.get('configured_subscriptions', 0)} |",
                f"| Parsed / usable nodes | {source_admission.get('parsed_nodes', 0)} / {source_admission.get('usable_nodes', 0)} |",
                f"| Informational nodes rejected | {source_admission.get('informational_rejected', 0)} |",
                f"| Name / multiplier filtered | {source_admission.get('name_filtered', 0)} / {source_admission.get('multiplier_filtered', 0)} |",
            ]
        )

    endpoint = proof.get("endpoint_qualification")
    if isinstance(endpoint, dict):
        lines.extend(
            [
                f"| Endpoint qualification | {endpoint.get('status', 'unknown')} |",
                f"| TCP reachable / tested | {endpoint.get('reachable', 0)} / {endpoint.get('tested', 0)} |",
                f"| TCP quarantined / unreachable | {endpoint.get('quarantined', 0)} / {endpoint.get('unreachable', 0)} |",
                f"| UDP-native skipped | {endpoint.get('skipped_udp_native', 0)} |",
                f"| DNS inconclusive / timeout reserve | {endpoint.get('dns_inconclusive', 0)} / {endpoint.get('timeout_reserve', 0)} |",
            ]
        )

    dns = proof.get("dns")
    if isinstance(dns, dict):
        for label, key in (
            ("DNS compatibility", "compatibility"),
            ("DNS leak audit", "leak"),
            ("DNS runtime audit", "runtime"),
        ):
            row = dns.get(key)
            if isinstance(row, dict):
                lines.append(f"| {label} | {row.get('status', 'unknown')} |")

    promotion = proof.get("promotion_guard")
    if isinstance(promotion, dict):
        lines.extend(
            [
                f"| Promotion Guard | {promotion.get('status', 'unknown')} |",
                f"| Promotion admission state | {promotion.get('admission_state', 'legacy')} |",
                f"| Promotion Guard reason | {promotion.get('reason', 'other')} |",
                f"| Promotion Guard warnings | {promotion.get('warnings', 0)} |",
                f"| Promotion Guard violations | {promotion.get('violations', 0)} |",
            ]
        )

    qualification = proof.get("qualification_pipeline")
    if isinstance(qualification, dict):
        lines.append(f"| Unified qualification stages | {qualification.get('stages', 0)} |")
        timings = qualification.get("timings_ms")
        if isinstance(timings, dict):
            for name, duration in sorted(timings.items()):
                lines.append(f"| Qualification `{name}` | {duration} ms |")
        lines.append("")
        lines.append(render_qualification_observability_markdown(qualification))
    release = proof.get("release")
    if isinstance(release, dict):
        lines.append(f"| Release transaction | {release.get('status')} |")
        lines.append(
            f"| Production bytes changed | {str(release.get('production_changed')).lower()} |"
        )
    lines.extend(
        [
            "",
            "This proof contains aggregate metadata only; node names, servers, credentials, endpoint URLs, and subscription URLs are intentionally excluded.",
            "",
        ]
    )
    return "\n".join(lines)
