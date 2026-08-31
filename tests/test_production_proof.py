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
            "diagnostics": {"tested_nodes": 10, "selector_failures": 0},
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
    assert "SECRET-NODE-NAME" not in repr(proof)
    assert "secret.example.invalid" not in repr(proof)
    assert "SUPER-SECRET-PASSWORD" not in repr(proof)
    assert "SECRET-NODE-NAME" not in markdown
    assert "secret.example.invalid" not in markdown
    assert "SUPER-SECRET-PASSWORD" not in markdown
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
