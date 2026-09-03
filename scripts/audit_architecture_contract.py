#!/usr/bin/env python3
"""Fail CI when P27-P32 architecture boundaries regress."""

from __future__ import annotations

from pathlib import Path

from clash_relay.policy_contract import load_policy_contract
from clash_relay.policy_document import load_policy_document

ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def main() -> int:
    policy_document = load_policy_document(ROOT / "policies.yaml")
    contract = load_policy_contract(policy_document.document)

    # P27: routing shadow/audit are consumers of the declared contract, never a
    # second source of public selector names.
    for relative in (
        "src/clash_relay/routing_shadow.py",
        "src/clash_relay/routing_v2_audit.py",
    ):
        content = _text(relative)
        for group_name in contract.public_groups.values():
            if group_name in content:
                raise SystemExit(
                    f"architecture audit: {relative} hard-codes contract group {group_name!r}"
                )
        if "load_policy_contract" not in content:
            raise SystemExit(f"architecture audit: {relative} does not consume PolicyContract")

    # P28: production reachability and builder provider traversal are owned by RuntimeGraph.
    production_audit = _text("src/clash_relay/production_audit.py")
    if "RuntimeGraph" not in production_audit or "def _reachable_sources(" in production_audit:
        raise SystemExit("architecture audit: production audit bypasses RuntimeGraph")
    builder = _text("src/clash_relay/builder.py")
    if "RuntimeGraph" not in builder or ".provider_order(" not in builder:
        raise SystemExit("architecture audit: builder bypasses RuntimeGraph provider traversal")

    # P29/P31: workflow orchestration delegates business stages to application scripts.
    workflow = _text(".github/workflows/publish.yml")
    if "scripts/run_production_pipeline.py" not in workflow:
        raise SystemExit("architecture audit: publish workflow bypasses ProductionPipeline")
    if "scripts/check_promotion_guard.py" not in workflow:
        raise SystemExit("architecture audit: publish workflow bypasses Promotion Guard")
    if "python - <<" in workflow or "python - <<'PY'" in workflow:
        raise SystemExit(
            "architecture audit: publish workflow contains inline Python business logic"
        )

    # P30: split physical policy declarations must normalize before domain use.
    if not (ROOT / "schemas/policy-manifest.schema.json").is_file():
        raise SystemExit("architecture audit: Policy Model v2 schema is missing")
    if not (ROOT / "src/clash_relay/policy_document.py").is_file():
        raise SystemExit("architecture audit: Policy Model v2 normalizer is missing")

    # P31: promotion policy is a public declaration, not a hidden workflow threshold.
    if not (ROOT / "promotion-guard.yaml").is_file():
        raise SystemExit("architecture audit: promotion-guard.yaml is missing")

    print("architecture contract audit: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
