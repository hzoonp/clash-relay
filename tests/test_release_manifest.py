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
                "nodes": 1,
                "filtered_over_multiplier": 2,
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
    }


def _matrix() -> dict:
    return {"status": "passed", "channel": "stable", "validated_cores": ["v1.a", "v1.b"]}


def test_dry_run_release_manifest_uses_exact_bytes_and_is_aggregate_only() -> None:
    candidate_bytes = b"candidate: exact\n"
    manifest = build_release_manifest(
        candidate=_candidate(),
        candidate_bytes=candidate_bytes,
        audit=_audit(),
        qualification={"status": "passed", "policy_model_version": 2},
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
    assert manifest["runtime"] == {"groups": 1, "providers": 1, "unique_nodes": 1}
    assert manifest["sources"]["configured"] == 1
    assert manifest["sources"]["by_use"]["general"]["distinct_sources"] == 1
    encoded = json.dumps(manifest, ensure_ascii=False)
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
    assert "aggregate-only" in markdown
    assert digest in markdown
    assert "secret-node" not in markdown
