#!/usr/bin/env python3
"""Fail CI when the P27-P45 architecture boundaries regress."""

from __future__ import annotations

from pathlib import Path

from clash_relay.policy_contract import load_policy_contract
from clash_relay.policy_document import load_policy_document
from clash_relay.util import load_yaml_file

ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def main() -> int:
    policy_document = load_policy_document(ROOT / "policies.yaml")
    contract = load_policy_contract(policy_document.document)

    # P27/P35: declaration truth is explicit. No Python routing-name/default fallback.
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
    policy_contract = _text("src/clash_relay/policy_contract.py")
    routing_policy = _text("src/clash_relay/routing_policy_v2.py")
    if "_LEGACY_DEFAULT_CONTRACT" in policy_contract:
        raise SystemExit("architecture audit: legacy PolicyContract defaults remain")
    if "_default_document" in routing_policy:
        raise SystemExit("architecture audit: legacy Routing V2 defaults remain")
    if "routing policy and routing.contract are required" not in policy_contract:
        raise SystemExit("architecture audit: PolicyContract no longer fails closed")

    # P28: RuntimeGraph is the topology truth.
    production_audit = _text("src/clash_relay/production_audit.py")
    if "RuntimeGraph" not in production_audit or "def _reachable_sources(" in production_audit:
        raise SystemExit("architecture audit: production audit bypasses RuntimeGraph")
    builder = _text("src/clash_relay/builder.py")
    if "RuntimeGraph" not in builder or ".provider_order(" not in builder:
        raise SystemExit("architecture audit: builder bypasses RuntimeGraph provider traversal")

    # P33: Actions invokes one application entrypoint and contains no business pipeline.
    workflow = _text(".github/workflows/publish.yml")
    if workflow.count("python scripts/run_production_release.py") != 1:
        raise SystemExit(
            "architecture audit: publish workflow must invoke one production entrypoint"
        )
    forbidden_leaf_scripts = (
        "run_production_pipeline.py",
        "fetch_current_config.py",
        "check_promotion_guard.py",
        "validate_mihomo_matrix.py",
        "publish_release_bundle.py",
        "render_production_proof.py",
        "load_scheduler_history.py",
        "load_ai_qualification_cache.py",
        "publish_production_metrics.py",
    )
    for name in forbidden_leaf_scripts:
        if name in workflow:
            raise SystemExit(f"architecture audit: workflow directly orchestrates {name}")
    if "python - <<" in workflow or "python - <<'PY'" in workflow:
        raise SystemExit(
            "architecture audit: publish workflow contains inline Python business logic"
        )
    if len(workflow.splitlines()) >= 100:
        raise SystemExit("architecture audit: publish workflow is no longer a thin adapter")

    lifecycle = _text("src/clash_relay/production_lifecycle.py")
    ordered_stages = (
        "generation = self._generate()",
        "self._load_derived_state()",
        "binary = self._download_primary_mihomo()",
        "pipeline = self._qualify(binary)",
        "promotion = self._promotion_guard()",
        "matrix = self._validate_matrix(binary)",
        "release = self._publish_release()",
        "derived_state = self._persist_derived_state()",
        "proof = self._post_commit_proof(release=release)",
        "manifest = self._post_commit_manifest(",
        "metrics = self._persist_production_metrics()",
    )
    positions = [lifecycle.find(stage) for stage in ordered_stages]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise SystemExit("architecture audit: production lifecycle stage order regressed")
    if "finally:" not in lifecycle or "shutil.rmtree(self.paths.private_dir" not in lifecycle:
        raise SystemExit("architecture audit: private production state is not always cleaned")

    # P39: typed retry contract replaces exception-message matching and only transient may retry.
    qualification = _text("src/clash_relay/qualification_pipeline.py")
    reliability = _text("src/clash_relay/qualification_reliability.py")
    if "QualificationStageError" not in qualification or "parse_failure_category" not in qualification:
        raise SystemExit("architecture audit: P39 typed qualification contract is missing")
    if '"qualification stage rejected the candidate" in str(exc)' in qualification:
        raise SystemExit("architecture audit: qualification retry regressed to message matching")
    if "QualificationFailureCategory.TRANSIENT" not in qualification:
        raise SystemExit("architecture audit: retry is no longer transient-only")
    if "_whole_browsing_probe_transient" not in reliability:
        raise SystemExit("architecture audit: whole-probe transient classifier is missing")

    # P40: release progress wraps, but never replaces, the proven release transaction.
    if "ReleaseProgress" not in lifecycle or "ReleasePhase.PUBLISHED" not in lifecycle:
        raise SystemExit("architecture audit: explicit release progress contract is missing")
    if "publish_release_bundle.py" not in lifecycle:
        raise SystemExit("architecture audit: lifecycle bypasses the proven release transaction")
    release_reliability = _text("src/clash_relay/release_reliability.py")
    for phase in ("PREPARED", "QUALIFIED", "PROMOTED", "PUBLISHED", "VERIFIED"):
        if phase not in release_reliability:
            raise SystemExit(f"architecture audit: release progress lost {phase.lower()} phase")

    # P41: metrics are an explicit lifecycle-owned, aggregate-only best-effort stage.
    scheduler_publisher = _text("scripts/publish_scheduler_history.py")
    metrics_publisher = _text("scripts/publish_production_metrics.py")
    if "production_metrics" in scheduler_publisher or "build_metrics_run" in scheduler_publisher:
        raise SystemExit("architecture audit: production metrics leaked back into scheduler persistence")
    if 'stage="persist_production_metrics"' not in lifecycle or "best_effort=True" not in lifecycle:
        raise SystemExit("architecture audit: lifecycle does not own best-effort production metrics")
    for token in ("append_metrics_run", "metrics_summary", "production-metrics-v1"):
        if token not in metrics_publisher:
            raise SystemExit(f"architecture audit: production metrics publisher missing {token}")

    # P34/P42: canonical physical policy is current v2 with four owned domain fragments.
    raw_manifest = load_yaml_file(ROOT / "policies.yaml")
    expected_fragments = {"routing", "scheduling", "classification", "topology"}
    if not isinstance(raw_manifest, dict) or raw_manifest.get("version") != 2:
        raise SystemExit("architecture audit: canonical policies.yaml is not Policy Model v2")
    fragments = raw_manifest.get("fragments")
    if not isinstance(fragments, dict) or set(fragments) != expected_fragments:
        raise SystemExit("architecture audit: canonical Policy Model v2 fragment layout drifted")
    if policy_document.model_version != 2 or policy_document.deprecated:
        raise SystemExit("architecture audit: canonical Policy Model v2 is not current")
    policy_loader = _text("src/clash_relay/policy_document.py")
    for section, owner in (
        ("routing", "routing"),
        ("scheduler", "scheduling"),
        ("probes", "scheduling"),
        ("capabilities", "classification"),
        ("pools", "topology"),
    ):
        if f'"{section}": "{owner}"' not in policy_loader:
            raise SystemExit(
                f"architecture audit: Policy Model v2 ownership missing {section}->{owner}"
            )
    if not (ROOT / "scripts/migrate_policy_v2.py").is_file():
        raise SystemExit("architecture audit: Policy Model v2 migration tool is missing")

    # P36: generic Services are an explicit compatibility-only extension, not production truth.
    services = load_yaml_file(ROOT / "services.yaml")
    if services != {"version": 1, "services": []}:
        raise SystemExit("architecture audit: canonical services.yaml gained production semantics")

    # P31/P37: promotion policy and aggregate release proof remain explicit public contracts.
    if not (ROOT / "promotion-guard.yaml").is_file():
        raise SystemExit("architecture audit: promotion-guard.yaml is missing")
    if not (ROOT / "src/clash_relay/release_manifest.py").is_file():
        raise SystemExit("architecture audit: aggregate release manifest is missing")
    if (
        "release-manifest.json" not in lifecycle
        or "render_release_manifest_markdown" not in lifecycle
    ):
        raise SystemExit("architecture audit: production lifecycle omits P37 release manifest")

    # P43/P44/P45: chaos matrix, doctor-first UX, and stabilization version are explicit.
    if not (ROOT / "tests/test_p43_chaos_matrix.py").is_file():
        raise SystemExit("architecture audit: P43 chaos matrix is missing")
    doctor = _text("src/clash_relay/doctor.py")
    for token in ("policy_model_version", "enabled_subscription_secrets", "first_publish_default"):
        if token not in doctor:
            raise SystemExit(f"architecture audit: doctor-first Fork UX missing {token}")
    pyproject = _text("pyproject.toml")
    package = _text("src/clash_relay/__init__.py")
    if 'version = "1.8.1"' not in pyproject or '__version__ = "1.8.1"' not in package:
        raise SystemExit("architecture audit: P45 v1.8.1 version boundary is incomplete")

    print("architecture contract audit: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
