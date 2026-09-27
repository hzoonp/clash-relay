from __future__ import annotations

import json

from clash_relay.qualification_performance import build_qualification_performance_evidence


def test_failure_planes_distinguish_dns_tcp_https_udp_and_ai() -> None:
    evidence = build_qualification_performance_evidence(
        host_report={
            "dns_inconclusive": 2,
            "quarantined": 3,
            "by_failure_category": {"nxdomain": 2, "no_answer": 1},
        },
        endpoint_report={
            "attempts": 3,
            "admission_quorum": 1,
            "dns_inconclusive": 1,
            "timeout_reserve": 4,
            "quarantined": 3,
            "robust_endpoints": 9,
            "reserve_endpoints": 5,
            "by_failure_category": {
                "connection_refused": 1,
                "network_unreachable": 1,
                "connect_failure": 1,
            },
        },
        browsing_summary={
            "diagnostics": {
                "attempts_per_node": 3,
                "required_successes": 2,
                "successful_samples": 21,
                "failed_samples": 9,
                "outcomes": {
                    "success": 21,
                    "missing_delay": 4,
                    "probe_error": 3,
                    "controller_http_503": 2,
                },
            },
            "transport_qualification": {
                "diagnostics": {
                    "tcp_failed_nodes": 2,
                    "udp_failed_nodes": 3,
                    "selector_failures": 1,
                    "static_udp_disabled_nodes": 2,
                    "tcp_attempts": 2,
                    "tcp_required_successes": 1,
                    "udp_timeout_ms": 700,
                }
            },
        },
        ai_summary={
            "service_evidence": {
                "openai": {
                    "live_tested": 5,
                    "live_passed": 3,
                    "live_failed": 1,
                    "inconclusive": 1,
                    "systemic_failure_detected": False,
                },
                "claude": {
                    "live_tested": 4,
                    "live_passed": 2,
                    "live_failed": 1,
                    "inconclusive": 1,
                    "systemic_failure_detected": True,
                },
            }
        },
    )

    assert evidence["failure_planes"]["dns"] == {
        "nxdomain": 2,
        "no_answer": 1,
        "confirmed_unresolvable": 3,
        "inconclusive": 3,
        "quarantined": 3,
    }
    assert evidence["failure_planes"]["tcp"] == {
        "connection_refused": 1,
        "network_unreachable": 1,
        "connect_failure": 1,
        "timeout_reserve": 4,
        "quarantined": 3,
        "robust": 9,
        "reserve": 5,
    }
    assert evidence["failure_planes"]["https"]["controller_http_errors"] == 2
    assert evidence["failure_planes"]["udp"]["udp_failed_nodes"] == 3
    assert evidence["failure_planes"]["ai"] == {
        "live_tested": 9,
        "live_passed": 5,
        "live_failed": 2,
        "inconclusive": 2,
        "systemic_failures": 1,
    }
    assert evidence["tuning"]["endpoint_timeout_action"] == "reserve"
    assert evidence["tuning"]["dns_inconclusive_action"] == "keep"
    assert evidence["tuning"]["transport_udp_timeout_ms"] == 700


def test_failure_plane_projection_never_copies_private_labels() -> None:
    evidence = build_qualification_performance_evidence(
        host_report={
            "by_failure_category": {
                "nxdomain": 1,
                "https://secret.example/token": 999,
            },
            "server": "secret.example",
        },
        endpoint_report={
            "by_failure_category": {
                "connection_refused": 1,
                "private-node": 999,
            },
            "server": "10.0.0.8",
        },
        browsing_summary={
            "diagnostics": {
                "outcomes": {
                    "probe_error": 1,
                    "private-outcome-secret": 999,
                },
                "node": "SECRET-NODE",
            }
        },
        ai_summary={
            "service_evidence": {
                "private-service-name": {
                    "live_tested": 2,
                    "live_passed": 1,
                    "live_failed": 1,
                    "inconclusive": 0,
                    "systemic_failure_detected": False,
                    "credential": "SECRET-TOKEN",
                }
            }
        },
    )

    serialized = json.dumps(evidence, sort_keys=True)
    for secret in (
        "secret.example",
        "10.0.0.8",
        "SECRET-NODE",
        "SECRET-TOKEN",
        "private-node",
        "private-outcome-secret",
        "private-service-name",
    ):
        assert secret not in serialized
