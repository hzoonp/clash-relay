from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from clash_relay.release_manifest import build_release_manifest, render_release_manifest_markdown


def _candidate() -> dict:
    return {
        "proxy-groups": [
            {"name": "Public", "type": "select", "use": ["provider-a"], "proxies": []}
        ],
        "proxy-providers": {
            "provider-a": {
                "type": "inline",
                "payload": [
                    {
                        "name": "[GENERAL] sub_1/secret-node #abc",
                        "type": "ss",
                        "server": "secret.example",
                        "port": 443,
                        "cipher": "aes-128-gcm",
                        "password": "do-not-leak",
                    }
                ],
            }
        },
        "rules": ["MATCH,Public"],
    }


def _audit() -> dict:
    return {
        "status": "passed",
        "subscriptions": [
            {
                "id": "subscription_1",
                "status": "ok",
                "input_nodes": 5,
                "parsed_valid_nodes": 4,
                "skipped_invalid_nodes": 1,
                "filtered_by_name": 1,
                "nodes": 2,
                "filtered_over_multiplier": 1,
                "post_dedup_nodes": 2,
                "runtime_nodes": 1,
            }
        ],
        "pools": [
            {
                "id": "general",
                "source_use": "general",
                "providers": 1,
                "nodes": 1,
                "sources": {"subscription_1": 1},
            }
        ],
        "dns_leak_audit": {
            "status": "passed",
            "mode": "strict_tun",
            "policy_rulesets": 2,
        },
        "dns_routing_policy": {
            "status": "compiled",
            "mode": "acl4ssr",
            "total_rulesets": 15,
        },
    }


def _matrix() -> dict:
    return {"status": "passed", "channel": "stable", "validated_cores": ["v1.a", "v1.b"]}


def test_dry_run_release_manifest_uses_exact_bytes_and_is_aggregate_only() -> None:
    candidate_bytes = b"candidate: exact\n"
    manifest = build_release_manifest(
        candidate=_candidate(),
        candidate_bytes=candidate_bytes,
        audit=_audit(),
        qualification={
            "status": "passed",
            "policy_model_version": 2,
            "qualification_removed_unique_nodes": 1,
            "qualification_removed_runtime_entries": 2,
            "sources_fully_removed": [
                {
                    "source": "sub_4",
                    "unique_nodes": 1,
                    "final_unique_nodes": 0,
                    "runtime_entries": 2,
                    "final_runtime_entries": 0,
                    "removed_at_stage": "ai",
                    "by_stage": {"browsing": 1, "ai": 1},
                    "by_failure_category": {
                        "browsing_qualification_failed": 1,
                        "ai_qualification_failed": 1,
                    },
                    "server": "secret.example.invalid",
                }
            ],
            "ai": {
                "service_evidence": {
                    "openai": {
                        "evidence_status": "passed",
                        "evidence_source": "live",
                        "systemic_failure_detected": False,
                        "lkg_fresh": False,
                        "live_tested": 2,
                        "live_passed": 2,
                        "live_failed": 0,
                        "inconclusive": 0,
                        "blocked_critical_endpoints": ["secret.example.invalid"],
                    }
                }
            },
        },
        promotion_guard={"status": "skipped", "reason": "dry_run"},
        matrix=_matrix(),
        release=None,
        publication_status="dry-run",
        policy_model_version=2,
        commit_sha="abc123",
        generated_at=datetime(2026, 9, 4, tzinfo=UTC),
    )

    digest = hashlib.sha256(candidate_bytes).hexdigest()
    assert manifest["release_id"] == digest
    assert manifest["config_sha256"] == digest
    assert manifest["config_bytes"] == len(candidate_bytes)
    assert manifest["public_config_version"] == 2
    assert manifest["runtime"] == {"groups": 1, "providers": 1, "unique_nodes": 1}
    assert manifest["sources"]["configured"] == 1
    assert manifest["sources"]["input_nodes"] == 5
    assert manifest["sources"]["parsed_valid_nodes"] == 4
    assert manifest["sources"]["skipped_invalid_nodes"] == 1
    assert manifest["sources"]["filtered_by_name"] == 1
    assert manifest["sources"]["accepted_nodes"] == 2
    assert manifest["sources"]["filtered_over_multiplier"] == 1
    assert manifest["sources"]["post_dedup_nodes"] == 2
    assert manifest["sources"]["runtime_nodes"] == 1
    assert manifest["sources"]["by_use"]["general"]["distinct_sources"] == 1
    assert manifest["dns_security"] == {
        "leak_audit": "passed",
        "tun_mode": "strict_tun",
        "routing_policy": "acl4ssr",
        "policy_rulesets": 15,
    }
    assert manifest["qualification"]["sources_fully_removed"][0]["removed_at_stage"] == "ai"
    assert (
        manifest["qualification"]["ai_service_evidence"]["openai"][
            "blocked_critical_endpoint_count"
        ]
        == 1
    )
    encoded = json.dumps(manifest, ensure_ascii=False)
    markdown = render_release_manifest_markdown(manifest)
    assert "| openai | passed | live | false | false | 2 / 2 / 0 / 0 | 1 |" in markdown
    for secret in ("subscription_1", "sub_1", "secret-node", "secret.example", "do-not-leak"):
        assert secret not in encoded


def test_published_manifest_uses_release_transaction_identity() -> None:
    candidate_bytes = b"candidate: exact\n"
    digest = hashlib.sha256(candidate_bytes).hexdigest()
    release = {
        "status": "published",
        "release_id": digest,
        "previous_release_id": "f" * 64,
        "sha256": digest,
        "bytes": len(candidate_bytes),
        "production_changed": True,
    }
    manifest = build_release_manifest(
        candidate=_candidate(),
        candidate_bytes=candidate_bytes,
        audit=_audit(),
        qualification={"status": "passed"},
        promotion_guard={"status": "passed", "reason": "within_thresholds", "violations": []},
        matrix=_matrix(),
        release=release,
        publication_status="published",
        policy_model_version=2,
        generated_at=datetime(2026, 9, 4, tzinfo=UTC),
    )

    assert manifest["release_status"] == "published"
    assert manifest["production_changed"] is True
    assert manifest["previous_release_id"] == "f" * 64
    markdown = render_release_manifest_markdown(manifest)
    assert "Public Config: **v2**" in markdown
    assert "aggregate-only" in markdown
    assert "DNS leak audit: **passed**" in markdown
    assert digest in markdown
    assert "secret-node" not in markdown
