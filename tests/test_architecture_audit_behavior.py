"""The architecture audit must judge behavior, not source spelling.

Each case below is a local rewrite that preserves the boundary, or a real
violation that must still fail. Renaming a variable or restructuring an
equivalent expression must not change the verdict.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

RELATIVE = "src/clash_relay/mihomo_serializer.py"
LIFECYCLE = "src/clash_relay/production_lifecycle.py"
RELEASE_STAGE = "src/clash_relay/production_release_stage.py"
QUALIFICATION = "src/clash_relay/qualification_pipeline.py"
COMPILER = "src/clash_relay/policy_compiler.py"
BUILDER = "src/clash_relay/builder.py"


def _load_audit(repo_root: Path):
    path = repo_root / "scripts" / "audit_architecture_contract.py"
    spec = importlib.util.spec_from_file_location("test_architecture_audit", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read(repo_root: Path, relative: str) -> str:
    return (repo_root / relative).read_text(encoding="utf-8")


def test_equivalent_serializer_rewrite_passes(repo_root: Path) -> None:
    module = _load_audit(repo_root)
    source = _read(repo_root, RELATIVE).replace(
        "    return graph.candidate",
        "    detached = copy.deepcopy(graph.candidate)\n    return detached",
    )
    source = source.replace("from typing import Any", "import copy\nfrom typing import Any", 1)

    module._audit_serializer(source)


def test_serializer_without_a_snapshot_return_fails(repo_root: Path) -> None:
    module = _load_audit(repo_root)
    source = _read(repo_root, RELATIVE).replace(
        "    return graph.candidate",
        "    return {}",
    )

    with pytest.raises(SystemExit, match="reads private graph state"):
        module._audit_serializer(source)


def test_serializer_renaming_the_graph_parameter_still_detaches(repo_root: Path) -> None:
    """The check follows the parameter name instead of a hard-coded identifier."""
    module = _load_audit(repo_root)
    source = _read(repo_root, RELATIVE).replace("graph: RuntimeGraph", "compiled: RuntimeGraph")
    source = source.replace("graph.candidate", "compiled.candidate")

    module._audit_serializer(source)


@pytest.mark.parametrize(
    "returned",
    [
        "return dict(graph.candidate)",
        "return {**graph.candidate}",
        "return copy.deepcopy(graph.candidate)",
    ],
)
def test_shallow_snapshot_derivations_pass(repo_root: Path, returned: str) -> None:
    """Every derivation of ``graph.candidate`` is isolated: the property deep-copies."""
    module = _load_audit(repo_root)
    source = _read(repo_root, RELATIVE).replace("    return graph.candidate", f"    {returned}")
    if "copy." in returned:
        source = source.replace("from typing import Any", "import copy\nfrom typing import Any", 1)

    module._audit_serializer(source)


def test_serializer_handing_over_a_local_snapshot_passes(repo_root: Path) -> None:
    module = _load_audit(repo_root)
    source = _read(repo_root, RELATIVE).replace(
        "    return graph.candidate",
        "    snapshot = graph.candidate\n    return snapshot",
    )

    module._audit_serializer(source)


def test_serializer_reading_private_state_fails(repo_root: Path) -> None:
    """The one real alias: the private mapping is the live compiled document."""
    module = _load_audit(repo_root)
    source = _read(repo_root, RELATIVE).replace(
        "    return graph.candidate",
        "    return graph._candidate",
    )

    with pytest.raises(SystemExit, match="reads private graph state"):
        module._audit_serializer(source)


def test_lifecycle_rename_of_locals_still_passes(repo_root: Path) -> None:
    module = _load_audit(repo_root)
    source = _read(repo_root, LIFECYCLE)
    source = source.replace(
        "binary = self._download_primary_mihomo()", "core = self._download_primary_mihomo()"
    )
    source = source.replace("pipeline = self._qualify(binary)", "pipeline = self._qualify(core)")
    source = source.replace(
        "release_stage = self._release_candidate_stage(project, binary)",
        "release_stage = self._release_candidate_stage(project, core)",
    )
    source = source.replace(
        "promotion = release_stage.promotion", "promotion = release_stage.promotion"
    )
    source = source.replace("matrix = release_stage.matrix", "matrix = release_stage.matrix")
    source = source.replace("release = release_stage.release", "release = release_stage.release")

    module._audit_lifecycle(source)


def test_lifecycle_stage_reorder_fails(repo_root: Path) -> None:
    module = _load_audit(repo_root)
    source = _read(repo_root, LIFECYCLE)
    proof = "            proof = self._post_commit_proof(release=release)\n"
    manifest = (
        "            manifest = self._post_commit_manifest(\n"
        "                promotion=promotion,\n"
        "                matrix=matrix,\n"
        "                release=release,\n"
        "            )\n"
    )
    assert proof in source and manifest in source
    source = source.replace(proof, "").replace(manifest, manifest + proof)

    with pytest.raises(SystemExit, match="production lifecycle stage order regressed"):
        module._audit_lifecycle(source)


def test_lifecycle_missing_finally_cleanup_fails(repo_root: Path) -> None:
    module = _load_audit(repo_root)
    source = re.sub(
        r"\n\s+finally:\n\s+self\._cleanup_private_state\(\)\n",
        "\n",
        _read(repo_root, LIFECYCLE),
    )
    if source == _read(repo_root, LIFECYCLE):
        source = re.sub(
            r"\n\s+finally:\n(?:\s+.*\n)+?(?=\n\S|\Z)",
            "\n",
            _read(repo_root, LIFECYCLE),
            count=1,
        )

    with pytest.raises(SystemExit, match="private production state is not always cleaned"):
        module._audit_lifecycle(source)


def test_release_stage_reorder_fails(repo_root: Path) -> None:
    module = _load_audit(repo_root)
    source = _read(repo_root, RELEASE_STAGE)
    matrix_call = "        matrix = validate_mihomo_matrix(\n"
    publish_call = "        release = publish_production_release(\n"
    assert matrix_call in source and publish_call in source

    # Move the publication call ahead of the matrix call while keeping the file
    # parseable: only the call sites swap, their surrounding blocks stay put.
    reordered = source.replace(matrix_call, "__MATRIX__", 1)
    reordered = reordered.replace(publish_call, matrix_call, 1)
    reordered = reordered.replace("__MATRIX__", publish_call, 1)

    with pytest.raises(SystemExit, match="delegated release-candidate stage order regressed"):
        module._audit_release_stage(reordered)


def test_release_stage_losing_qualification_input_fails(repo_root: Path) -> None:
    module = _load_audit(repo_root)
    source = _read(repo_root, RELEASE_STAGE).replace(
        "                qualification_path=paths.qualification,\n",
        "",
    )

    with pytest.raises(SystemExit, match="Promotion Guard lost qualification sidecar input"):
        module._audit_release_stage(source)


def test_qualification_message_matching_fails(repo_root: Path) -> None:
    module = _load_audit(repo_root)
    source = _read(repo_root, QUALIFICATION) + (
        "\n\ndef _regressed(exc: Exception) -> bool:\n"
        "    return 'qualification stage rejected the candidate' in str(exc)\n"
    )

    with pytest.raises(SystemExit, match="qualification retry regressed to message matching"):
        module._audit_qualification(source)


def test_qualification_subprocess_regression_fails(repo_root: Path) -> None:
    module = _load_audit(repo_root)
    source = _read(repo_root, QUALIFICATION) + (
        "\n\ndef _regressed(path):\n    import subprocess\n    return subprocess.run(path)\n"
    )

    with pytest.raises(SystemExit, match="qualification retained Python IPC token"):
        module._audit_qualification(source)


def test_qualification_losing_the_application_api_fails(repo_root: Path) -> None:
    module = _load_audit(repo_root)
    source = _read(repo_root, QUALIFICATION).replace("run_browsing_qualification(", "legacy_run(")

    with pytest.raises(SystemExit, match="qualification bypasses application API"):
        module._audit_qualification(source)


def test_compiler_boundary_accepts_a_local_rename(repo_root: Path) -> None:
    module = _load_audit(repo_root)
    compiler = _read(repo_root, COMPILER).replace(
        "graph = RuntimeGraph.from_candidate(output)",
        "frozen = RuntimeGraph.from_candidate(output)",
    )
    compiler = compiler.replace("graph.provider_order(", "frozen.provider_order(")
    compiler = compiler.replace("graph=graph", "graph=frozen")

    module._audit_compiler_boundary(compiler, _read(repo_root, BUILDER))


def test_compiler_without_freezing_the_graph_fails(repo_root: Path) -> None:
    module = _load_audit(repo_root)
    compiler = _read(repo_root, COMPILER).replace("RuntimeGraph.from_candidate(output)", "output")

    with pytest.raises(SystemExit, match="does not freeze/traverse RuntimeGraph"):
        module._audit_compiler_boundary(compiler, _read(repo_root, BUILDER))


def test_builder_regaining_topology_fails(repo_root: Path) -> None:
    module = _load_audit(repo_root)
    builder = _read(repo_root, BUILDER) + (
        "\n\ndef _regressed(graph):\n    return RuntimeGraph.from_candidate(graph)\n"
    )

    with pytest.raises(SystemExit, match="builder bypasses compiler/serializer boundary"):
        module._audit_compiler_boundary(_read(repo_root, COMPILER), builder)
