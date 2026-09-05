#!/usr/bin/env python3
"""Fail CI when the P51 operational SLO boundary regresses."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def main() -> int:
    slo = _text("src/clash_relay/operational_slo.py")
    application = _text("src/clash_relay/slo_application.py")
    lifecycle = _text("src/clash_relay/production_lifecycle.py")
    metrics = _text("src/clash_relay/production_metrics.py")

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
        raise SystemExit("operational SLO audit: qualification classification parses exception text")
    if "_MAX_ATTEMPTS = 60" not in slo:
        raise SystemExit("operational SLO audit: bounded attempt ring changed unexpectedly")

    if 'key_name=f"{production_key}.operational-slo-v1"' not in application:
        raise SystemExit("operational SLO audit: SLO state is not isolated from production config")
    if "operational-slo-v1" in metrics:
        raise SystemExit("operational SLO audit: failed-attempt SLOs leaked into success-only metrics")

    for token in (
        "persist_operational_slo(",
        "ProductionOutcome.QUALIFICATION_REJECTED",
        "ProductionOutcome.PROMOTION_BLOCKED",
        "ProductionOutcome.PASSED",
    ):
        if token not in lifecycle:
            raise SystemExit(f"operational SLO audit: lifecycle missing outcome path {token}")
    if "failure_retry_attempted=qualification_retry_attempted(exc)" not in lifecycle:
        raise SystemExit("operational SLO audit: typed retry failure evidence is not recorded")
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
        if token in slo or token in application:
            raise SystemExit(f"operational SLO audit: forbidden persisted field token {token}")

    print("operational SLO contract audit: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
