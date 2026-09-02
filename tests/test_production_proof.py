from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from clash_relay.errors import ValidationError
from clash_relay.production_proof import build_production_proof, render_production_proof_markdown


def _inputs(candidate_path: Path) -> dict:
    return {
        "candidate_path": candidate_path,
        "audit": {
            "status": "passed",
            "reachability": {
                "status": "passed",
                "groups_checked": 30,
                "routing_surfaces_checked": 25,
                "runtime_rules_checked": 22,
            },
            "openai_client_path": {
                "status": "passed",
                "selection": "stable_first_fallback",
                "runtime_regions": 2,
                "runtime_providers": 2,
                "runtime_nodes": 3,
                "health_check": {
                    "url": "SHOULD-NOT-LEAK",
                    "name": "SHOULD-NOT-LEAK",
                },
            },
        },
        "browsing": {
            "status": "qualified",
            "diagnostics": {"tested_nodes": 10},
            "qualified_nodes": 8,
            "stable_nodes": 6,
            "reserve_nodes": 2,
            "failed_nodes": 2,
            "automatic_nodes": 6,
        },
        "ai": {
            "status": "qualified",
            "diagnostics": {
                "tested_nodes": 10,
                "selector_failures": 0,
                "openai_app": {
                    "critical": {
                        "app_ready_live_nodes": 3,
                        "endpoint_count": 4,
                        "tls_errors": 2,
                        "dns_errors": 1,
                        "timeouts": 3,
                        "probes": {
                            "openai_app_android": {"url": "SHOULD-NOT-LEAK"},
                        },
                    },
                    "supporting": {
                        "endpoint_count": 4,
                        "tls_errors": 1,
                        "probes": {
                            "openai_support_workos": {"url": "SHOULD-NOT-LEAK"},
                        },
                    },
                },
            },
            "service_qualified_nodes": {"openai": 3, "claude": 4, "gemini": 8},
            "service_fail_closed": [],
        },
        "validated_cores": ("v1.19.30", "v1.19.29"),
        "publication_status": "published",
    }


def test_production_proof_contains_only_aggregate_candidate_metadata(tmp_path: Path) -> None:
    candidate = tmp_path / "config.yaml"
    content = (
        b"proxy-providers:\n"
        b"  private:\n"
        b"    type: inline\n"
        b"    payload:\n"
        b"      - name: SECRET-NODE-NAME\n"
        b"        type: http\n"
        b"        server: secret.example.invalid\n"
        b"        port: 443\n"
        b"        password: SUPER-SECRET-PASSWORD\n"
        b"proxy-groups: []\n"
        b"rule-providers: {}\n"
        b"rules: []\n"
    )
    candidate.write_bytes(content)

    proof = build_production_proof(**_inputs(candidate))
    markdown = render_production_proof_markdown(proof)

    assert proof["status"] == "passed"
    assert proof["candidate"]["bytes"] == len(content)
    assert proof["candidate"]["sha256"] == hashlib.sha256(content).hexdigest()
    assert proof["browsing"] == {
        "tested": 10,
        "qualified": 8,
        "stable": 6,
        "reserve": 2,
        "rejected": 2,
        "automatic": 6,
    }
    assert proof["ai"]["service_qualified"] == {"claude": 4, "gemini": 8, "openai": 3}
    assert proof["ai"]["openai_app"] == {
        "app_ready_live_nodes": 3,
        "critical_endpoints": 4,
        "critical_tls_errors": 2,
        "critical_dns_errors": 1,
        "critical_timeouts": 3,
        "supporting_endpoints": 4,
        "supporting_tls_errors": 1,
    }
    assert proof["ai"]["openai_client_path"] == {
        "status": "passed",
        "selection": "stable_first_fallback",
        "runtime_regions": 2,
        "runtime_providers": 2,
        "runtime_nodes": 3,
    }
    assert "OpenAI App-ready live nodes | 3" in markdown
    assert "OpenAI critical TLS / DNS / timeout failures | 2 / 1 / 3" in markdown
    assert "OpenAI client-path selection | stable_first_fallback" in markdown
    assert "OpenAI client-path nodes | 3" in markdown
    for secret in (
        "SECRET-NODE-NAME",
        "secret.example.invalid",
        "SUPER-SECRET-PASSWORD",
        "SHOULD-NOT-LEAK",
        "openai_app_android",
        "openai_support_workos",
    ):
        assert secret not in repr(proof)
        assert secret not in markdown
    assert "v1.19.30" in markdown
    assert "v1.19.29" in markdown


def test_production_proof_rejects_failed_reachability_audit(tmp_path: Path) -> None:
    candidate = tmp_path / "config.yaml"
    candidate.write_text("proxy-providers: {}\nproxy-groups: []\nrule-providers: {}\nrules: []\n")
    inputs = _inputs(candidate)
    inputs["audit"]["reachability"]["status"] = "failed"

    with pytest.raises(ValidationError, match="passed source reachability audit"):
        build_production_proof(**inputs)


def test_production_proof_rejects_duplicate_core_versions(tmp_path: Path) -> None:
    candidate = tmp_path / "config.yaml"
    candidate.write_text("proxy-providers: {}\nproxy-groups: []\nrule-providers: {}\nrules: []\n")
    inputs = _inputs(candidate)
    inputs["validated_cores"] = ("v1.19.30", "v1.19.30")

    with pytest.raises(ValidationError, match="unique validated Mihomo core versions"):
        build_production_proof(**inputs)
