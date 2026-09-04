#!/usr/bin/env python3
"""Fail CI when the P27-P48 architecture boundaries regress."""

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

    # P28/P47: RuntimeGraph is topology truth and builder crosses one compiler/serializer boundary.
    production_audit = _text("src/clash_relay/production_audit.py")
    if "RuntimeGraph" not in production_audit or "def _reachable_sources(" in production_audit:
        raise SystemExit("architecture audit: production audit bypasses RuntimeGraph")

    builder = _text("src/clash_relay/builder.py")
    compiler = _text("src/clash_relay/policy_compiler.py")
    serializer = _text("src/clash_relay/mihomo_serializer.py")
    if "compile_runtime_graph(" not in builder or "serialize_runtime_graph(" not in builder:
        raise SystemExit("architecture audit: builder bypasses compiler/serializer boundary")
    if "generate_config(" in builder or "RuntimeGraph" in builder or ".provider_order(" in builder:
        raise SystemExit("architecture audit: builder resumed topology construction/traversal")
    for token in (
        "apply_acl4ssr_group_semantics",
        "apply_acl4ssr_source_exclusions",
        "harden_browsing_runtime",
        "_expose_manual_provider_choices",
    ):
        if token in builder:
            raise SystemExit(f"architecture audit: builder resumed post-generation pass {token}")
        if token not in compiler:
            raise SystemExit(f"architecture audit: PolicyCompiler no longer owns pass {token}")
    if "RuntimeGraph.from_candidate(output)" not in compiler or ".provider_order(" not in compiler:
        raise SystemExit("architecture audit: PolicyCompiler does not freeze/traverse RuntimeGraph")
    if "copy.deepcopy(dict(graph.candidate))" not in serializer:
        raise SystemExit("architecture audit: Mihomo serializer no longer detaches compiled graph")

    for path in (ROOT / "src" / "clash_relay").glob("*.py"):
        if path.name in {"generator.py", "policy_compiler.py"}:
            continue
        if "generate_config(" in path.read_text(encoding="utf-8"):
            raise SystemExit(
                f"architecture audit: low-level runtime draft generator escaped PolicyCompiler: {path.name}"
            )

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
        "self._load_derived_state(project)",
        "binary = self._download_primary_mihomo()",
        "pipeline = self._qualify(binary)",
        "promotion = self._promotion_guard(project)",
        "matrix = self._validate_matrix(binary)",
        "release = self._publish_release(project)",
        "derived_state = self._persist_derived_state(project)",
        "proof = self._post_commit_proof(release=release)",
        "manifest = self._post_commit_manifest(",
        "metrics = self._persist_production_metrics(project)",
    )
    positions = [lifecycle.find(stage) for stage in ordered_stages]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise SystemExit("architecture audit: production lifecycle stage order regressed")
    if "finally:" not in lifecycle or "shutil.rmtree(self.paths.private_dir" not in lifecycle:
        raise SystemExit("architecture audit: private production state is not always cleaned")

    # P39/P48: qualification retry is typed and package-to-package, never stdout JSON IPC.
    qualification = _text("src/clash_relay/qualification_pipeline.py")
    reliability = _text("src/clash_relay/qualification_reliability.py")
    if "QualificationStageRejected" not in qualification:
        raise SystemExit("architecture audit: typed qualification rejection contract is missing")
    if '"qualification stage rejected the candidate" in str(exc)' in qualification:
        raise SystemExit("architecture audit: qualification retry regressed to message matching")
    if "exc.retryable" not in qualification or "_whole_browsing_probe_transient" not in reliability:
        raise SystemExit(
            "architecture audit: transient-only qualification retry contract regressed"
        )
    for token in (
        "subprocess",
        "sys.executable",
        "_run_json_stage",
        "script_dir",
        "python_executable",
        "qualify_browsing.py",
        "qualify_ai.py",
        "harden_openai_runtime.py",
    ):
        if token in qualification:
            raise SystemExit(f"architecture audit: qualification retained Python IPC token {token}")
    for token in (
        "run_browsing_qualification(",
        "run_ai_qualification(",
        "harden_openai_client_path(",
    ):
        if token not in qualification:
            raise SystemExit(f"architecture audit: qualification bypasses application API {token}")

    # P48: lifecycle calls typed package services directly; scripts are adapters only.
    production_pipeline = _text("src/clash_relay/production_pipeline.py")
    for token in ("script_dir", "python_executable"):
        if token in production_pipeline:
            raise SystemExit(
                f"architecture audit: production pipeline retained subprocess API {token}"
            )
    for token in (
        "subprocess",
        "sys.executable",
        "_script_command",
        "_run_command",
        "scripts_dir",
    ):
        if token in lifecycle:
            raise SystemExit(
                f"architecture audit: production lifecycle retained Python IPC token {token}"
            )
    for token in (
        "download_pinned_mihomo(",
        "validate_mihomo_matrix(",
        "fetch_current_production_config(",
        "run_promotion_guard(",
        "publish_production_release(",
        "persist_ai_qualification_cache(",
        "persist_scheduler_history(",
        "persist_production_metrics(",
        "render_production_proof_application(",
    ):
        if token not in lifecycle:
            raise SystemExit(f"architecture audit: lifecycle bypasses application service {token}")

    production_application = _text("src/clash_relay/production_application.py")
    mihomo_matrix_application = _text("src/clash_relay/mihomo_matrix_application.py")
    if "commit_release_bundle(" not in production_application:
        raise SystemExit(
            "architecture audit: in-process publication bypasses proven release transaction"
        )
    if "download_pinned_mihomo(" not in mihomo_matrix_application:
        raise SystemExit(
            "architecture audit: Mihomo matrix still delegates download through a script"
        )
    for relative in (
        "scripts/qualify_browsing.py",
        "scripts/qualify_ai.py",
        "scripts/harden_openai_runtime.py",
        "scripts/download_mihomo.py",
        "scripts/validate_mihomo_matrix.py",
        "scripts/fetch_current_config.py",
        "scripts/check_promotion_guard.py",
        "scripts/publish_release_bundle.py",
        "scripts/render_production_proof.py",
        "scripts/load_scheduler_history.py",
        "scripts/load_ai_qualification_cache.py",
        "scripts/publish_scheduler_history.py",
        "scripts/publish_ai_qualification_cache.py",
        "scripts/publish_production_metrics.py",
    ):
        if "subprocess" in _text(relative):
            raise SystemExit(
                f"architecture audit: thin adapter {relative} still launches subprocesses"
            )

    # P40: release progress wraps the proven in-process release transaction.
    if "ReleaseProgress" not in lifecycle or "ReleasePhase.PUBLISHED" not in lifecycle:
        raise SystemExit("architecture audit: explicit release progress contract is missing")
    release_reliability = _text("src/clash_relay/release_reliability.py")
    for phase in ("PREPARED", "QUALIFIED", "PROMOTED", "PUBLISHED", "VERIFIED"):
        if phase not in release_reliability:
            raise SystemExit(f"architecture audit: release progress lost {phase.lower()} phase")

    # P41: metrics remain an explicit lifecycle-owned, aggregate-only best-effort stage.
    if '"persist_production_metrics"' not in lifecycle or "_best_effort_state(" not in lifecycle:
        raise SystemExit(
            "architecture audit: lifecycle does not own best-effort production metrics"
        )
    for token in ("append_metrics_run", "metrics_summary", "production-metrics-v1"):
        if token not in production_application:
            raise SystemExit(
                f"architecture audit: production application missing metrics token {token}"
            )

    # P34/P42: canonical physical policy is current v2 with four owned domain fragments.
    raw_manifest = load_yaml_file(ROOT / "policies.yaml")
    expected_fragments = {"routing", "scheduling", "classification", "topology"}
    if not isinstance(raw_manifest, dict) or raw_manifest.get("version") != 2:
        raise SystemExit("architecture audit: canonical policies.yaml is not Policy Model v2")
    fragments = raw_manifest.get("fragments")
    if not isinstance(fragments, dict) or set(fragments) != expected_fragments:
        raise SystemExit("architecture audit: canonical Policy Model v2 fragment layout drifted")
    if policy_document.model_version != 2:
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

    # P46: runtime is clean-slate v2; migration is offline and generic Services are gone.
    if "Policy Model v2 manifest is required" not in policy_loader:
        raise SystemExit("architecture audit: runtime no longer rejects Policy Model v1")
    if "deprecated=True" in policy_loader or "compatibility_status" in policy_loader:
        raise SystemExit("architecture audit: Policy Model v1 runtime compatibility returned")
    if "return policies, 1" in qualification or "historical v1 contract" in qualification:
        raise SystemExit("architecture audit: qualification retained Policy Model v1 fallback")
    for relative in (
        "services.yaml",
        "schemas/services.schema.json",
        "tests/fixtures/project/services.yaml",
    ):
        if (ROOT / relative).exists():
            raise SystemExit(f"architecture audit: removed Services artifact returned: {relative}")
    for relative in (
        "src/clash_relay/config_loader.py",
        "src/clash_relay/builder.py",
        "src/clash_relay/cli.py",
        "src/clash_relay/doctor.py",
        "src/clash_relay/production_lifecycle.py",
        "src/clash_relay/production_pipeline.py",
    ):
        content = _text(relative)
        if "services_path" in content or '"--services"' in content:
            raise SystemExit(
                f"architecture audit: {relative} retained generic Services runtime API"
            )

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
