from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from clash_relay.production_proof import build_production_proof
from clash_relay.release_manifest import build_release_manifest


def _proof_inputs(candidate_path: Path) -> dict:
    return {
        "candidate_path": candidate_path,
        "audit": {
            "status": "passed",
            "reachability": {
                "status": "passed",
                "groups_checked": 1,
                "routing_surfaces_checked": 1,
                "runtime_rules_checked": 1,
            },
        },
        "browsing": {
            "status": "qualified",
            "diagnostics": {"tested_nodes": 1},
            "qualified_nodes": 1,
            "stable_nodes": 1,
            "reserve_nodes": 0,
            "failed_nodes": 0,
            "automatic_nodes": 1,
        },
        "ai": {
            "status": "qualified",
            "diagnostics": {"tested_nodes": 1, "selector_failures": 0},
            "service_qualified_nodes": {"openai": 1},
            "service_fail_closed": [],
        },
        "validated_cores": ("v1.19.30", "v1.19.29"),
        "publication_status": "preflight",
    }


def _manifest_candidate() -> dict:
    return {
        "proxy-groups": [
            {"name": "Public", "type": "select", "use": ["provider-a"], "proxies": []}
        ],
        "proxy-providers": {
            "provider-a": {
                "type": "inline",
                "payload": [
                    {
                        "name": "fictional",
                        "type": "http",
                        "server": "public.invalid.example",
                        "port": 443,
                    }
                ],
            }
        },
        "rules": ["MATCH,Public"],
    }


def test_production_proof_accepts_preflight_without_release_metadata(tmp_path: Path) -> None:
    candidate = tmp_path / "config.yaml"
    candidate.write_text(
        "proxy-providers: {}\nproxy-groups: []\nrule-providers: {}\nrules: []\n",
        encoding="utf-8",
    )

    proof = build_production_proof(**_proof_inputs(candidate))

    assert proof["status"] == "passed"
    assert proof["publication"] == "preflight"
    assert "release" not in proof


def test_release_manifest_accepts_preflight_without_release_transaction() -> None:
    candidate_bytes = b"candidate: preflight\n"
    manifest = build_release_manifest(
        candidate=_manifest_candidate(),
        candidate_bytes=candidate_bytes,
        audit={"status": "passed", "subscriptions": [], "pools": []},
        qualification={"status": "passed", "policy_model_version": 2},
        promotion_guard={"status": "passed", "reason": "within_thresholds", "violations": []},
        matrix={
            "status": "passed",
            "channel": "stable",
            "validated_cores": ["v1.19.30", "v1.19.29"],
        },
        release=None,
        publication_status="preflight",
        policy_model_version=2,
        generated_at=datetime(2026, 9, 12, tzinfo=UTC),
    )

    assert manifest["publication_status"] == "preflight"
    assert manifest["release_id"] == hashlib.sha256(candidate_bytes).hexdigest()
    assert "release_status" not in manifest
    assert "production_changed" not in manifest


def test_preflight_adapter_no_longer_rewrites_dry_run_evidence(repo_root: Path) -> None:
    source = (repo_root / "src/clash_relay/production_preflight.py").read_text(encoding="utf-8")

    assert 'publication_status="preflight"' in source
    assert 'publication_status="dry-run"' not in source
    assert 'proof["publication"] = "preflight"' not in source
    assert 'manifest["publication_status"] = "preflight"' not in source
