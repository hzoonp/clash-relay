"""Fail-closed, privacy-safe readiness checks for source-only releases.

This module does not publish production configuration. It evaluates the
aggregate release manifest emitted by the canonical production lifecycle and
answers whether that evidence is strong enough to promote a source release or
release candidate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .errors import ValidationError

_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ReleaseReadinessPolicy:
    """Versioned expectations that a release manifest must satisfy."""

    public_config_version: int = 2
    policy_model_version: int = 2
    minimum_mihomo_cores: int = 2

    def __post_init__(self) -> None:
        if self.public_config_version < 1:
            raise ValidationError(
                "release readiness public config version must be positive"
            )
        if self.policy_model_version < 1:
            raise ValidationError(
                "release readiness policy model version must be positive"
            )
        if self.minimum_mihomo_cores < 1:
            raise ValidationError("release readiness requires at least one Mihomo core")


def assess_release_readiness(
    manifest: dict[str, Any],
    *,
    expected_commit_sha: str,
    policy: ReleaseReadinessPolicy | None = None,
) -> dict[str, Any]:
    """Return aggregate release-readiness evidence without sensitive material."""

    policy = policy or ReleaseReadinessPolicy()
    violations: list[str] = []

    if not _GIT_SHA.fullmatch(expected_commit_sha):
        raise ValidationError(
            "release readiness expected commit SHA must be exact lowercase Git SHA"
        )
    if manifest.get("publication_status") != "published":
        violations.append("publication_status")
    if manifest.get("release_status") != "published":
        violations.append("release_status")

    release_id = manifest.get("release_id")
    config_sha = manifest.get("config_sha256")
    if (
        not isinstance(release_id, str)
        or not _SHA256.fullmatch(release_id)
        or config_sha != release_id
    ):
        violations.append("release_identity")

    if manifest.get("commit_sha") != expected_commit_sha:
        violations.append("commit_sha")
    if manifest.get("public_config_version") != policy.public_config_version:
        violations.append("public_config_version")
    if manifest.get("policy_model_version") != policy.policy_model_version:
        violations.append("policy_model_version")

    promotion = manifest.get("promotion_guard")
    if (
        not isinstance(promotion, dict)
        or promotion.get("status") != "passed"
        or promotion.get("violations") != 0
    ):
        violations.append("promotion_guard")

    mihomo = manifest.get("mihomo")
    cores: list[str] = []
    if isinstance(mihomo, dict):
        raw_cores = mihomo.get("validated_cores")
        if isinstance(raw_cores, list):
            cores = [item for item in raw_cores if isinstance(item, str) and item]
    if (
        not isinstance(mihomo, dict)
        or mihomo.get("status") != "passed"
        or mihomo.get("channel") != "stable"
        or len(set(cores)) < policy.minimum_mihomo_cores
    ):
        violations.append("mihomo_matrix")

    previous = manifest.get("previous_release_id")
    if previous is not None and (
        not isinstance(previous, str)
        or not _SHA256.fullmatch(previous)
        or previous == release_id
    ):
        violations.append("rollback_identity")

    return {
        "status": "passed" if not violations else "blocked",
        "violations": sorted(violations),
        "public_config_version": policy.public_config_version,
        "policy_model_version": policy.policy_model_version,
        "minimum_mihomo_cores": policy.minimum_mihomo_cores,
    }


def validate_release_readiness(
    manifest: dict[str, Any],
    *,
    expected_commit_sha: str,
    policy: ReleaseReadinessPolicy | None = None,
) -> dict[str, Any]:
    """Fail closed when aggregate production evidence is not release-ready."""

    result = assess_release_readiness(
        manifest,
        expected_commit_sha=expected_commit_sha,
        policy=policy,
    )
    if result["status"] != "passed":
        violations = result["violations"]
        detail = ", ".join(str(item) for item in violations)
        raise ValidationError(f"release readiness blocked: {detail}")
    return result
