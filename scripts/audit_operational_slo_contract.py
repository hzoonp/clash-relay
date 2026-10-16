#!/usr/bin/env python3
"""Fail CI when the operational SLO boundary regresses."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def main() -> int:
    slo = _text("src/clash_relay/operational_slo.py")
    observability = _text("src/clash_relay/production_observability.py")
    lifecycle = _text("src/clash_relay/production_lifecycle.py")
    metrics = _text("src/clash_relay/production_metrics.py")
    kv_keys = _text("src/clash_relay/kv_keys.py")

    for token in (
        "QualificationStageRejected",
        "QualificationFailureCategory",
        "qualification_rejection_rate",
        "retry_recovery_rate",
        "promotion_guard_block_rate",
        "candidate_churn_rate",
        "lifecycle_duration_ms",
    ):
        if token not in slo:
            raise SystemExit(f"operational SLO audit: missing typed aggregate token {token}")

    if "str(error)" in slo or "error.args" in slo:
        raise SystemExit(
            "operational SLO audit: qualification classification parses exception text"
        )
    if "_MAX_ATTEMPTS = 60" not in slo:
        raise SystemExit("operational SLO audit: bounded attempt ring changed unexpectedly")

    # SLO state lives under its own derived key. The canonical suffix set lives in
    # kv_keys.py, so the isolation check follows the named suffix into the module
    # that consumes it instead of requiring the rebuilt literal in the caller.
    if "auxiliary_state_key(production_key, OPERATIONAL_SLO_SUFFIX)" not in observability:
        raise SystemExit("operational SLO audit: SLO state is not isolated from production config")
    if '".operational-slo-v1"' not in kv_keys:
        raise SystemExit("operational SLO audit: kv_keys lost the canonical operational SLO suffix")
    if "operational-slo-v1" in metrics:
        raise SystemExit(
            "operational SLO audit: failed-attempt SLOs leaked into success-only metrics"
        )

    for token in (
        "ProductionOutcome.QUALIFICATION_REJECTED",
        "ProductionOutcome.PROMOTION_BLOCKED",
        "ProductionOutcome.PASSED",
        "failure_retry_attempted=qualification_retry_attempted(error)",
    ):
        if token not in observability:
            raise SystemExit(f"operational SLO audit: observability missing outcome path {token}")

    for token in (
        "publish_post_release_observability(",
        "record_failure_observability(",
    ):
        if token not in lifecycle:
            raise SystemExit(
                f"operational SLO audit: lifecycle missing observability boundary {token}"
            )
    if "raise\n        finally:" not in lifecycle:
        raise SystemExit("operational SLO audit: original production failure is not re-raised")

    forbidden = (
        "subscription_url",
        "server_address",
        "raw_config",
        "stderr",
        "exception_message",
    )
    for token in forbidden:
        if token in slo or token in observability:
            raise SystemExit(f"operational SLO audit: forbidden persisted field token {token}")

    print("operational SLO contract audit: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
