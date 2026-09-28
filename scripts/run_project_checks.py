#!/usr/bin/env python3
"""Run the local and CI quality gates from one shared definition."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MYPY_OPTIONS = ("--follow-imports=skip", "--ignore-missing-imports", "--check-untyped-defs")
MYPY_TARGETS = (
    "builder",
    "carrier_qualification",
    "classify",
    "cli",
    "config_loader",
    "diagnose",
    "effective_config",
    "network_address_policy",
    "node_policy",
    "selector",
    "policy_compiler",
    "runtime_graph",
    "availability",
    "service_qualification",
    "service_qualification_result",
    "ai_application",
    "ai_runtime_reliability",
    "ai_service_qualification",
    "browsing_application",
    "browsing_runtime",
    "scheduler_history",
    "scheduler_policy",
    "qualification_pipeline",
    "qualification_performance",
    "qualification_pipeline_result",
    "qualification_reliability",
    "production_application",
    "production_pipeline",
    "production_diagnostics",
    "production_proof",
    "production_event_audit",
    "production_failure_metrics",
    "promotion_guard",
    "production_release_stage",
    "health_check_inventory",
    "release_bundle",
    "release_inventory",
    "release_reconciliation",
    "release_transaction",
    "publishers/cloudflare_kv",
    "operational_slo",
    "slo_application",
    "production_lifecycle",
    "production_lifecycle_result",
    "publication_decision",
)
AUDITS = (
    "audit_documentation_contract.py",
    "audit_architecture_contract.py",
    "audit_operational_slo_contract.py",
    "audit_client_dns_verification_contract.py",
    "audit_service_qualification_contract.py",
    "audit_v2_release_contract.py",
    "audit_supply_chain.py",
    "repository_audit.py",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gate", choices=("typecheck", "audit"))
    parser.add_argument("--offline-acl4ssr", action="store_true")
    args = parser.parse_args(argv)
    if args.offline_acl4ssr and args.gate != "audit":
        parser.error("--offline-acl4ssr requires the audit gate")

    if args.gate == "typecheck":
        command = [
            sys.executable,
            "-m",
            "mypy",
            *MYPY_OPTIONS,
            *(f"src/clash_relay/{target}.py" for target in MYPY_TARGETS),
        ]
        return subprocess.run(command, cwd=ROOT, check=False).returncode

    for audit in AUDITS:
        result = subprocess.run([sys.executable, f"scripts/{audit}"], cwd=ROOT, check=False)
        if result.returncode:
            return result.returncode
    if args.offline_acl4ssr:
        return subprocess.run(
            [sys.executable, "scripts/audit_acl4ssr_fidelity.py", "--offline"],
            cwd=ROOT,
            check=False,
        ).returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
