"""Aggregate qualification performance evidence and tuning parameters.

This module intentionally projects only bounded counters and static tuning
values. It must never copy node names, proxy endpoints, subscription URLs, or
credentials into production observability.
"""

from __future__ import annotations

from typing import Any


def _count(value: Any) -> int:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else 0
    )


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _category_count(report: dict[str, Any], name: str) -> int:
    categories = _mapping(report.get("by_failure_category"))
    return _count(categories.get(name))


def _controller_http_errors(outcomes: dict[str, Any]) -> int:
    return sum(
        _count(count)
        for name, count in outcomes.items()
        if isinstance(name, str) and name.startswith("controller_http_")
    )


def _ai_evidence(ai_summary: dict[str, Any]) -> dict[str, int]:
    evidence = _mapping(ai_summary.get("service_evidence"))
    live_tested = live_passed = live_failed = inconclusive = systemic_failures = 0
    for row in evidence.values():
        if not isinstance(row, dict):
            continue
        live_tested += _count(row.get("live_tested"))
        live_passed += _count(row.get("live_passed"))
        live_failed += _count(row.get("live_failed"))
        inconclusive += _count(row.get("inconclusive"))
        if row.get("systemic_failure_detected") is True:
            systemic_failures += 1
    return {
        "live_tested": live_tested,
        "live_passed": live_passed,
        "live_failed": live_failed,
        "inconclusive": inconclusive,
        "systemic_failures": systemic_failures,
    }


def build_qualification_performance_evidence(
    *,
    host_report: dict[str, Any],
    endpoint_report: dict[str, Any],
    browsing_summary: dict[str, Any],
    ai_summary: dict[str, Any],
) -> dict[str, Any]:
    """Build one privacy-safe view of failure planes and current tuning knobs."""

    browsing_diagnostics = _mapping(browsing_summary.get("diagnostics"))
    browsing_outcomes = _mapping(browsing_diagnostics.get("outcomes"))
    transport = _mapping(browsing_summary.get("transport_qualification"))
    transport_diagnostics = _mapping(transport.get("diagnostics"))

    nxdomain = _category_count(host_report, "nxdomain")
    no_answer = _category_count(host_report, "no_answer")
    connection_refused = _category_count(endpoint_report, "connection_refused")
    network_unreachable = _category_count(endpoint_report, "network_unreachable")
    connect_failure = _category_count(endpoint_report, "connect_failure")

    return {
        "authority": "aggregate_pre_publish_observability_only",
        "failure_planes": {
            "dns": {
                "nxdomain": nxdomain,
                "no_answer": no_answer,
                "confirmed_unresolvable": nxdomain + no_answer,
                "inconclusive": _count(host_report.get("dns_inconclusive"))
                + _count(endpoint_report.get("dns_inconclusive")),
                "quarantined": _count(host_report.get("quarantined")),
            },
            "tcp": {
                "connection_refused": connection_refused,
                "network_unreachable": network_unreachable,
                "connect_failure": connect_failure,
                "timeout_reserve": _count(endpoint_report.get("timeout_reserve")),
                "quarantined": _count(endpoint_report.get("quarantined")),
                "robust": _count(endpoint_report.get("robust_endpoints")),
                "reserve": _count(endpoint_report.get("reserve_endpoints")),
            },
            "https": {
                "successful_samples": _count(browsing_diagnostics.get("successful_samples")),
                "failed_samples": _count(browsing_diagnostics.get("failed_samples")),
                "missing_delay": _count(browsing_outcomes.get("missing_delay")),
                "probe_error": _count(browsing_outcomes.get("probe_error")),
                "controller_http_errors": _controller_http_errors(browsing_outcomes),
            },
            "udp": {
                "tcp_failed_nodes": _count(transport_diagnostics.get("tcp_failed_nodes")),
                "udp_failed_nodes": _count(transport_diagnostics.get("udp_failed_nodes")),
                "selector_failures": _count(transport_diagnostics.get("selector_failures")),
                "static_udp_disabled_nodes": _count(
                    transport_diagnostics.get("static_udp_disabled_nodes")
                ),
            },
            "ai": _ai_evidence(ai_summary),
        },
        "tuning": {
            "endpoint_attempts": _count(endpoint_report.get("attempts")),
            "endpoint_admission_quorum": _count(endpoint_report.get("admission_quorum")),
            "endpoint_timeout_action": "reserve",
            "dns_inconclusive_action": "keep",
            "browsing_attempts_per_node": _count(
                browsing_diagnostics.get("attempts_per_node")
            ),
            "browsing_required_successes": _count(
                browsing_diagnostics.get("required_successes")
            ),
            "transport_tcp_attempts": _count(transport_diagnostics.get("tcp_attempts")),
            "transport_tcp_required_successes": _count(
                transport_diagnostics.get("tcp_required_successes")
            ),
            "transport_udp_timeout_ms": _count(transport_diagnostics.get("udp_timeout_ms")),
        },
    }
