#!/usr/bin/env python3
"""Fail CI when the current architecture boundaries regress.

The checks judge behavior and AST boundaries instead of exact source spellings:
an equivalent rename or local rewrite passes, while a bypass, a reordered
stage, or a lost gate still fails.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

from clash_relay.errors import ConfigurationError
from clash_relay.policy_contract import load_policy_contract
from clash_relay.policy_document import _validate_fragment_owner, load_policy_document
from clash_relay.util import load_yaml_file

ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _parse(relative: str, text: str | None = None) -> ast.Module:
    source = _text(relative) if text is None else text
    try:
        return ast.parse(source)
    except SyntaxError as exc:
        raise SystemExit(f"architecture audit: cannot parse {relative}") from exc


def _walk_calls(node: ast.AST | list[ast.AST]) -> list[ast.Call]:
    roots = node if isinstance(node, list) else [node]
    calls = [item for root in roots for item in ast.walk(root) if isinstance(item, ast.Call)]
    return sorted(calls, key=lambda item: (item.lineno, item.col_offset))


def _call_name(call: ast.Call) -> str | None:
    func = call.func
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
        return ".".join(reversed(parts))
    return None


def _attr_chain(node: ast.AST) -> tuple[str, ...] | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return tuple(reversed(parts))
    return None


def _find_function(module: ast.Module, name: str, relative: str) -> ast.FunctionDef:
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise SystemExit(f"architecture audit: {relative} lost function {name}")


def _call_names(node: ast.AST) -> set[str]:
    return {name for name in (_call_name(call) for call in _walk_calls(node)) if name}


def _ordered_calls(node: ast.AST, names: Iterable[str]) -> list[str]:
    wanted = set(names)
    return [name for name in (_call_name(call) for call in _walk_calls(node)) if name in wanted]


def _require_call_order(
    node: ast.AST,
    names: Iterable[str],
    message: str,
) -> None:
    ordered = list(names)
    seen = _ordered_calls(node, ordered)
    positions: list[int] = []
    for name in ordered:
        if name not in seen:
            raise SystemExit(message)
        positions.append(seen.index(name))
    if positions != sorted(positions):
        raise SystemExit(message)


def _identifiers(module: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            chain = _attr_chain(node)
            if chain is not None:
                found.add(".".join(chain))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


def _string_constants(module: ast.Module) -> set[str]:
    return {
        node.value
        for node in ast.walk(module)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _function_names(module: ast.Module) -> set[str]:
    return {
        node.name
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _matches_exception_message(module: ast.Module) -> bool:
    """True when an exception is classified by its rendered message."""

    for node in ast.walk(module):
        if not isinstance(node, ast.Compare) or not any(isinstance(op, ast.In) for op in node.ops):
            continue
        operands = [node.left, *node.comparators]
        if any(isinstance(item, ast.Call) and _call_name(item) == "str" for item in operands):
            return True
    return False


def _serializer_uses_public_snapshot(text: str) -> bool:
    """The serializer must read the graph's public snapshot, never its internals.

    ``RuntimeGraph.candidate`` deep-copies on every access, so any value derived
    from it — ``dict(...)``, ``{**...}``, a local alias, or a second deep copy —
    is already isolated from the compiled graph. The only real aliasing risk is
    reaching into private state such as ``graph._candidate``, which hands back
    the live mapping. Behavior is proven by the nested-isolation test; this check
    only keeps the dependency on the public surface.
    """

    module = _parse("src/clash_relay/mihomo_serializer.py", text)
    function = _find_function(module, "serialize_runtime_graph", "mihomo_serializer.py")
    if not function.args.args:
        return False
    graph_parameter = function.args.args[0].arg
    uses_public = False
    for node in ast.walk(function):
        chain = _attr_chain(node)
        if chain is None or chain[0] != graph_parameter or len(chain) < 2:
            continue
        if chain[1].startswith("_"):
            return False
        if chain[1] == "candidate":
            uses_public = True
    return uses_public


def _audit_serializer(text: str | None = None) -> None:
    source = _text("src/clash_relay/mihomo_serializer.py") if text is None else text
    if not _serializer_uses_public_snapshot(source):
        raise SystemExit(
            "architecture audit: Mihomo serializer reads private graph state "
            "instead of the detached candidate snapshot"
        )


def _compiler_freezes_graph(text: str) -> bool:
    names = _call_names(_parse("src/clash_relay/policy_compiler.py", text))
    freezes = "RuntimeGraph.from_candidate" in names
    traverses = any(name.endswith(".provider_order") for name in names)
    return freezes and traverses


def _builder_respects_boundary(text: str) -> bool:
    module = _parse("src/clash_relay/builder.py", text)
    names = _call_names(module)
    identifiers = _identifiers(module)
    if "compile_runtime_graph" not in names or "serialize_runtime_graph" not in names:
        return False
    if any(name.endswith("generate_config") for name in names):
        return False
    if "RuntimeGraph" in identifiers:
        return False
    return not any(name.endswith(".provider_order") for name in names)


def _audit_compiler_boundary(compiler_text: str, builder_text: str) -> None:
    if not _compiler_freezes_graph(compiler_text):
        raise SystemExit("architecture audit: PolicyCompiler does not freeze/traverse RuntimeGraph")
    if not _builder_respects_boundary(builder_text):
        raise SystemExit("architecture audit: builder bypasses compiler/serializer boundary")


def _lifecycle_stage_order_is_intact(text: str) -> bool:
    module = _parse("src/clash_relay/production_lifecycle.py", text)
    run = _find_function(module, "run", "production_lifecycle.py")
    stages = (
        "self._generate",
        "self._load_derived_state",
        "self._download_primary_mihomo",
        "self._qualify",
        "self._release_candidate_stage",
        "self._persist_derived_state",
        "self._post_commit_proof",
        "self._post_commit_manifest",
        "publish_post_release_observability",
    )
    seen = _ordered_calls(run, stages)
    positions: list[int] = []
    for stage in stages:
        if stage not in seen:
            return False
        positions.append(seen.index(stage))
    return positions == sorted(positions)


def _private_state_always_cleaned(text: str) -> bool:
    module = _parse("src/clash_relay/production_lifecycle.py", text)
    run = _find_function(module, "run", "production_lifecycle.py")
    for node in ast.walk(run):
        if not isinstance(node, ast.Try) or not node.finalbody:
            continue
        for call in _walk_calls(node.finalbody):
            if _call_name(call) not in {"shutil.rmtree", "rmtree"}:
                continue
            for argument in call.args:
                chain = _attr_chain(argument)
                if chain is not None and chain[-1] == "private_dir":
                    return True
    return False


def _audit_lifecycle(text: str | None = None) -> None:
    source = _text("src/clash_relay/production_lifecycle.py") if text is None else text
    if not _lifecycle_stage_order_is_intact(source):
        raise SystemExit("architecture audit: production lifecycle stage order regressed")
    if not _private_state_always_cleaned(source):
        raise SystemExit("architecture audit: private production state is not always cleaned")


def _release_stage_is_ordered(text: str) -> bool:
    module = _parse("src/clash_relay/production_release_stage.py", text)
    function = _find_function(module, "run_release_candidate_stage", "production_release_stage.py")
    stages = (
        "fetch_current_production_config",
        "run_promotion_guard",
        "validate_mihomo_matrix",
        "publish_production_release",
    )
    seen = _ordered_calls(function, stages)
    positions: list[int] = []
    for stage in stages:
        if stage not in seen:
            return False
        positions.append(seen.index(stage))
    return positions == sorted(positions)


def _promotion_guard_keeps_qualification_input(text: str) -> bool:
    """Structural tripwire only.

    The behavioral contract — the guard receives the qualification sidecar — is
    asserted by ``tests/test_production_release_stage.py``, which runs the stage
    and inspects the arguments actually passed. This check just keeps the call
    site from silently dropping the keyword.
    """

    module = _parse("src/clash_relay/production_release_stage.py", text)
    function = _find_function(module, "run_release_candidate_stage", "production_release_stage.py")
    for call in _walk_calls(function):
        if _call_name(call) != "run_promotion_guard":
            continue
        if any(keyword.arg == "qualification_path" for keyword in call.keywords):
            return True
    return False


def _audit_release_stage(text: str | None = None) -> None:
    source = _text("src/clash_relay/production_release_stage.py") if text is None else text
    if not _release_stage_is_ordered(source):
        raise SystemExit("architecture audit: delegated release-candidate stage order regressed")
    if not _promotion_guard_keeps_qualification_input(source):
        raise SystemExit("architecture audit: Promotion Guard lost qualification sidecar input")


def _audit_qualification(text: str | None = None) -> None:
    """Structural tripwires for the qualification boundary.

    The retry contract itself — only typed ``retryable`` rejections retry, and a
    policy rejection does not — is behavioral and owned by
    ``tests/test_qualification_pipeline.py`` and
    ``tests/test_qualification_reliability.py``. This audit only keeps the
    typed rejection from being replaced by string matching or a subprocess
    boundary.
    """

    source = _text("src/clash_relay/qualification_pipeline.py") if text is None else text
    module = _parse("src/clash_relay/qualification_pipeline.py", source)
    names = _call_names(module)
    if "QualificationStageRejected" not in _identifiers(module) and not any(
        name.endswith("QualificationStageRejected") for name in names
    ):
        raise SystemExit("architecture audit: typed qualification rejection contract is missing")
    if _matches_exception_message(module):
        raise SystemExit("architecture audit: qualification retry regressed to message matching")
    identifiers = _identifiers(module)
    forbidden = {
        "subprocess",
        "sys.executable",
        "_run_json_stage",
        "script_dir",
        "python_executable",
        "openai_application",
        "harden_openai_client_path",
    }
    leaked = sorted(token for token in forbidden if token in identifiers)
    if leaked:
        raise SystemExit(f"architecture audit: qualification retained Python IPC token {leaked[0]}")
    for required in (
        "run_browsing_qualification",
        "run_ai_qualification",
        "harden_declared_service_client_paths",
    ):
        if required not in names:
            raise SystemExit(
                f"architecture audit: qualification bypasses application API {required}"
            )
    if not _qualification_returns_model_version(module):
        raise SystemExit("architecture audit: qualification retained Policy Model v1 fallback")


def _qualification_returns_model_version(module: ast.Module) -> bool:
    function = _find_function(module, "_qualification_policy_input", "qualification_pipeline.py")
    for node in ast.walk(function):
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Tuple):
            continue
        if any(
            _attr_chain(item) and _attr_chain(item)[-1] == "model_version"
            for item in node.value.elts
        ):
            return True
    return False


_PROCESS_LAUNCHES = frozenset(
    {
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "os.system",
        "os.spawnl",
        "os.spawnv",
        "os.execv",
        "os.fork",
    }
)


def _process_launch_calls(module: ast.Module) -> list[ast.Call]:
    """Calls that actually start or replace a process.

    ``subprocess.TimeoutExpired`` and ``subprocess.Popen[bytes]`` annotations are
    the shared runtime's vocabulary, not a second process boundary, so they must
    not trip this check.
    """

    return [call for call in _walk_calls(module) if _call_name(call) in _PROCESS_LAUNCHES]


def _audit_probe_runtime() -> None:
    module = _parse("src/clash_relay/mihomo_probe_runtime.py")
    names = _call_names(module)
    for token in (
        "free_tcp_port",
        "run_config_test",
        "start_mihomo_process",
        "stop_mihomo_process",
        "wait_for_process_condition",
    ):
        if token not in names and token not in _function_names(module):
            raise SystemExit(f"architecture audit: shared Mihomo probe runtime missing {token}")
    for relative in (
        "src/clash_relay/browsing_qualification.py",
        "src/clash_relay/ai_qualification.py",
        "src/clash_relay/transport_qualification.py",
    ):
        probe = _parse(relative)
        probe_names = _call_names(probe)
        probe_identifiers = _identifiers(probe)
        if "_free_port" in _function_names(probe):
            raise SystemExit(
                f"architecture audit: {relative} retained Mihomo lifecycle token def _free_port("
            )
        launches = _process_launch_calls(probe)
        if launches:
            raise SystemExit(
                f"architecture audit: {relative} retained Mihomo lifecycle token "
                f"{_call_name(launches[0])}"
            )
        for forbidden in ("os.killpg", "signal.SIGTERM", "signal.SIGKILL"):
            if forbidden in probe_identifiers:
                raise SystemExit(
                    f"architecture audit: {relative} retained Mihomo lifecycle token {forbidden}"
                )
        for required in (
            "run_config_test",
            "start_mihomo_process",
            "stop_mihomo_process",
            "wait_for_process_condition",
        ):
            if required not in probe_names:
                raise SystemExit(
                    f"architecture audit: {relative} bypasses shared Mihomo runtime {required}"
                )


def _audit_no_python_ipc(relative: str, forbidden: Iterable[str]) -> None:
    identifiers = _identifiers(_parse(relative))
    leaked = sorted(token for token in forbidden if token in identifiers)
    if leaked:
        raise SystemExit(f"architecture audit: {relative} retained Python IPC token {leaked[0]}")


def _audit_application_calls(relative: str, required: Iterable[str], message: str) -> None:
    names = _call_names(_parse(relative))
    for token in required:
        if token not in names:
            raise SystemExit(message.format(token=token))


def _audit_generate_config_escape() -> None:
    for path in (ROOT / "src" / "clash_relay").glob("*.py"):
        if path.name in {"generator.py", "policy_compiler.py"}:
            continue
        module = _parse(str(path.relative_to(ROOT)))
        if any(name.endswith("generate_config") for name in _call_names(module)):
            raise SystemExit(
                "architecture audit: low-level runtime draft generator escaped "
                f"PolicyCompiler: {path.name}"
            )


def _audit_thin_adapters() -> None:
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
    ):
        identifiers = _identifiers(_parse(relative))
        if "subprocess" in identifiers or any(
            token.startswith("subprocess.") for token in identifiers
        ):
            raise SystemExit(
                f"architecture audit: thin adapter {relative} still launches subprocesses"
            )


def _audit_routing_declarations(contract) -> None:
    for relative in (
        "src/clash_relay/routing_shadow.py",
        "src/clash_relay/routing_v2_audit.py",
    ):
        module = _parse(relative)
        constants = _string_constants(module)
        for group_name in contract.public_groups.values():
            if group_name in constants:
                raise SystemExit(
                    f"architecture audit: {relative} hard-codes contract group {group_name!r}"
                )
        if "load_policy_contract" not in _identifiers(module):
            raise SystemExit(f"architecture audit: {relative} does not consume PolicyContract")


def _audit_production_audit_ownership() -> None:
    module = _parse("src/clash_relay/production_audit.py")
    if "RuntimeGraph" not in _identifiers(module):
        raise SystemExit("architecture audit: production audit bypasses RuntimeGraph")
    if "_reachable_sources" in _function_names(module):
        raise SystemExit("architecture audit: production audit bypasses RuntimeGraph")


def _audit_policy_loader() -> None:
    module = _parse("src/clash_relay/policy_document.py")
    identifiers = _identifiers(module)
    if "compatibility_status" in identifiers:
        raise SystemExit("architecture audit: Policy Model v1 runtime compatibility returned")
    for call in _walk_calls(module):
        if any(
            keyword.arg == "deprecated"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in call.keywords
        ):
            raise SystemExit("architecture audit: Policy Model v1 runtime compatibility returned")
    if "Policy Model v2 manifest is required" not in _string_constants(module):
        raise SystemExit("architecture audit: runtime no longer rejects Policy Model v1")


def _audit_policy_ownership() -> None:
    expected = {
        "routing": "routing",
        "scheduler": "scheduling",
        "probes": "scheduling",
        "capabilities": "classification",
        "pools": "topology",
    }
    for section, owner in expected.items():
        _validate_fragment_owner(owner, section)
    for section, wrong_owner in (("pools", "routing"), ("scheduler", "topology")):
        try:
            _validate_fragment_owner(wrong_owner, section)
        except ConfigurationError:
            continue
        raise SystemExit(
            f"architecture audit: Policy Model v2 ownership missing {section}->{expected[section]}"
        )


def main() -> int:
    policy_document = load_policy_document(ROOT / "policies.yaml")
    contract = load_policy_contract(policy_document.document)

    # Declaration truth is explicit. No Python routing-name/default fallback.
    _audit_routing_declarations(contract)
    policy_contract = _text("src/clash_relay/policy_contract.py")
    routing_policy = _text("src/clash_relay/routing_policy_v2.py")
    if "_LEGACY_DEFAULT_CONTRACT" in policy_contract:
        raise SystemExit("architecture audit: legacy PolicyContract defaults remain")
    if "_default_document" in routing_policy:
        raise SystemExit("architecture audit: legacy Routing V2 defaults remain")
    if "routing policy and routing.contract are required" not in policy_contract:
        raise SystemExit("architecture audit: PolicyContract no longer fails closed")

    # RuntimeGraph is topology truth and builder crosses one compiler/serializer boundary.
    _audit_production_audit_ownership()
    _audit_compiler_boundary(
        _text("src/clash_relay/policy_compiler.py"),
        _text("src/clash_relay/builder.py"),
    )
    _audit_serializer()

    compiler = _text("src/clash_relay/policy_compiler.py")
    builder = _text("src/clash_relay/builder.py")
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

    _audit_generate_config_escape()

    # Actions invokes one application entrypoint and contains no business pipeline.
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

    _audit_lifecycle()
    _audit_release_stage()

    # Qualification retry is typed and package-to-package, never stdout JSON IPC.
    _audit_qualification()
    reliability = _parse("src/clash_relay/qualification_reliability.py")
    if "_whole_browsing_probe_transient" not in _function_names(reliability):
        raise SystemExit(
            "architecture audit: transient-only qualification retry contract regressed"
        )

    # Production probe services share one Mihomo process/runtime boundary.
    _audit_probe_runtime()

    # Lifecycle calls typed package services directly; scripts are adapters only.
    _audit_no_python_ipc(
        "src/clash_relay/production_pipeline.py",
        ("script_dir", "python_executable"),
    )
    _audit_no_python_ipc(
        "src/clash_relay/production_lifecycle.py",
        ("subprocess", "sys.executable", "_script_command", "_run_command", "scripts_dir"),
    )
    _audit_no_python_ipc(
        "src/clash_relay/production_release_stage.py",
        ("subprocess", "sys.executable", "_script_command", "_run_command", "scripts_dir"),
    )
    _audit_application_calls(
        "src/clash_relay/production_lifecycle.py",
        (
            "download_pinned_mihomo",
            "run_release_candidate_stage",
            "persist_ai_qualification_cache",
            "persist_scheduler_history",
            "publish_post_release_observability",
            "record_failure_observability",
            "render_production_proof_application",
        ),
        "architecture audit: lifecycle bypasses application service {token}",
    )
    _audit_application_calls(
        "src/clash_relay/production_release_stage.py",
        (
            "fetch_current_production_config",
            "run_promotion_guard",
            "validate_mihomo_matrix",
            "publish_production_release",
        ),
        "architecture audit: release-candidate stage bypasses application service {token}",
    )

    production_application = _text("src/clash_relay/production_application.py")
    kv_keys = _text("src/clash_relay/kv_keys.py")
    mihomo_matrix_application = _text("src/clash_relay/mihomo_matrix_application.py")
    if "commit_release_bundle(" not in production_application:
        raise SystemExit(
            "architecture audit: in-process publication bypasses proven release transaction"
        )
    if "download_pinned_mihomo(" not in mihomo_matrix_application:
        raise SystemExit(
            "architecture audit: Mihomo matrix still delegates download through a script"
        )
    _audit_thin_adapters()

    # Release progress wraps the proven in-process release transaction.
    lifecycle = _text("src/clash_relay/production_lifecycle.py")
    if "ReleaseProgress" not in lifecycle or "ReleasePhase.PUBLISHED" not in lifecycle:
        raise SystemExit("architecture audit: explicit release progress contract is missing")
    release_reliability = _text("src/clash_relay/release_reliability.py")
    for phase in ("PREPARED", "QUALIFIED", "PROMOTED", "PUBLISHED", "VERIFIED"):
        if phase not in release_reliability:
            raise SystemExit(f"architecture audit: release progress lost {phase.lower()} phase")

    # Aggregate observability is consolidated behind one best-effort boundary.
    observability = _parse("src/clash_relay/production_observability.py")
    observability_names = _call_names(observability) | _function_names(observability)
    if "persist_production_metrics" not in observability_names or "_best_effort" not in (
        observability_names
    ):
        raise SystemExit(
            "architecture audit: consolidated observability lost production metrics ownership"
        )
    if "_publish_scheduler_observation" not in observability_names:
        raise SystemExit(
            "architecture audit: consolidated observability lost scheduler evidence ownership"
        )
    if "_record_operational_slo" not in observability_names:
        raise SystemExit(
            "architecture audit: consolidated observability lost operational SLO ownership"
        )
    for token in ("append_metrics_run", "metrics_summary", "PRODUCTION_METRICS_SUFFIX"):
        if token not in production_application:
            raise SystemExit(
                f"architecture audit: production application missing metrics token {token}"
            )
    # The canonical derived KV key suffixes live in kv_keys.py, where the complete
    # name set is length-validated; the application must consume that name rather
    # than rebuild the suffix privately.
    if '".production-metrics-v1"' not in kv_keys:
        raise SystemExit("architecture audit: kv_keys lost the canonical metrics key suffix")

    # Canonical physical policy is current v2 with four owned domain fragments.
    raw_manifest = load_yaml_file(ROOT / "policies.yaml")
    expected_fragments = {"routing", "scheduling", "classification", "topology"}
    if not isinstance(raw_manifest, dict) or raw_manifest.get("version") != 2:
        raise SystemExit("architecture audit: canonical policies.yaml is not Policy Model v2")
    fragments = raw_manifest.get("fragments")
    if not isinstance(fragments, dict) or set(fragments) != expected_fragments:
        raise SystemExit("architecture audit: canonical Policy Model v2 fragment layout drifted")
    if policy_document.model_version != 2:
        raise SystemExit("architecture audit: canonical Policy Model v2 is not current")
    _audit_policy_ownership()
    _audit_policy_loader()
    if not (ROOT / "scripts/migrate_policy_v2.py").is_file():
        raise SystemExit("architecture audit: Policy Model v2 migration tool is missing")

    # Runtime is clean-slate v2; migration is offline and generic Services are gone.
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
        identifiers = _identifiers(_parse(relative))
        if "services_path" in identifiers or any(
            token == "--services" for token in _string_constants(_parse(relative))
        ):
            raise SystemExit(
                f"architecture audit: {relative} retained generic Services runtime API"
            )

    # Promotion policy and aggregate release proof remain explicit public contracts.
    if not (ROOT / "promotion-guard.yaml").is_file():
        raise SystemExit("architecture audit: promotion-guard.yaml is missing")
    if not (ROOT / "src/clash_relay/release_manifest.py").is_file():
        raise SystemExit("architecture audit: aggregate release manifest is missing")
    if (
        "release-manifest.json" not in lifecycle
        or "render_release_manifest_markdown" not in lifecycle
    ):
        raise SystemExit("architecture audit: production lifecycle omits release manifest")

    # Chaos, doctor-first Fork UX, and package version remain explicit release contracts.
    if not (ROOT / "tests/test_chaos_matrix.py").is_file():
        raise SystemExit("architecture audit: chaos matrix is missing")
    doctor = _text("src/clash_relay/doctor.py")
    for token in ("policy_model_version", "enabled_subscription_secrets", "first_publish_default"):
        if token not in doctor:
            raise SystemExit(f"architecture audit: doctor-first Fork UX missing {token}")
    pyproject = _text("pyproject.toml")
    package = _text("src/clash_relay/__init__.py")
    if 'version = "2.3.0"' not in pyproject or '__version__ = "2.3.0"' not in package:
        raise SystemExit("architecture audit: v2.3.0 package version boundary is incomplete")
    if not (ROOT / "scripts" / "audit_v2_release_contract.py").is_file():
        raise SystemExit("architecture audit: v2 release contract audit is missing")

    print("architecture contract audit: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
