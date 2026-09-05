from __future__ import annotations

import pytest

from clash_relay.errors import ValidationError
from clash_relay.release_readiness import (
    ReleaseReadinessPolicy,
    assess_release_readiness,
    validate_release_readiness,
)

COMMIT_SHA = "a" * 40
RELEASE_ID = "b" * 64
PREVIOUS_ID = "c" * 64


def _manifest() -> dict:
    return {
        "publication_status": "published",
        "release_status": "published",
        "release_id": RELEASE_ID,
        "config_sha256": RELEASE_ID,
        "commit_sha": COMMIT_SHA,
        "public_config_version": 2,
        "policy_model_version": 2,
        "promotion_guard": {
            "status": "passed",
            "reason": "within_thresholds",
            "violations": 0,
        },
        "mihomo": {
            "status": "passed",
            "channel": "stable",
            "validated_cores": ["v1.19.29", "v1.19.30"],
        },
        "previous_release_id": PREVIOUS_ID,
    }


def test_release_readiness_accepts_exact_aggregate_production_evidence() -> None:
    result = validate_release_readiness(_manifest(), expected_commit_sha=COMMIT_SHA)

    assert result == {
        "status": "passed",
        "violations": [],
        "public_config_version": 2,
        "policy_model_version": 2,
        "minimum_mihomo_cores": 2,
    }


def test_release_readiness_blocks_dry_run_or_unbound_commit() -> None:
    manifest = _manifest()
    manifest["publication_status"] = "dry-run"
    manifest["commit_sha"] = "d" * 40

    result = assess_release_readiness(manifest, expected_commit_sha=COMMIT_SHA)

    assert result["status"] == "blocked"
    assert result["violations"] == ["commit_sha", "publication_status"]
    with pytest.raises(ValidationError, match="commit_sha, publication_status"):
        validate_release_readiness(manifest, expected_commit_sha=COMMIT_SHA)


def test_release_readiness_blocks_identity_guard_matrix_and_version_regressions() -> None:
    manifest = _manifest()
    manifest["config_sha256"] = "d" * 64
    manifest["public_config_version"] = 1
    manifest["policy_model_version"] = 1
    manifest["promotion_guard"] = {"status": "blocked", "violations": 1}
    manifest["mihomo"] = {
        "status": "passed",
        "channel": "stable",
        "validated_cores": ["v1.19.30"],
    }
    manifest["previous_release_id"] = RELEASE_ID

    result = assess_release_readiness(manifest, expected_commit_sha=COMMIT_SHA)

    assert result["status"] == "blocked"
    assert result["violations"] == [
        "mihomo_matrix",
        "policy_model_version",
        "promotion_guard",
        "public_config_version",
        "release_identity",
        "rollback_identity",
    ]


def test_release_readiness_policy_is_fail_closed() -> None:
    with pytest.raises(ValidationError, match="Public Config"):
        ReleaseReadinessPolicy(public_config_version=0)
    with pytest.raises(ValidationError, match="policy model"):
        ReleaseReadinessPolicy(policy_model_version=0)
    with pytest.raises(ValidationError, match="Mihomo"):
        ReleaseReadinessPolicy(minimum_mihomo_cores=0)
    with pytest.raises(ValidationError, match="exact lowercase Git SHA"):
        assess_release_readiness(_manifest(), expected_commit_sha="not-a-sha")
