from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

FULL_SHA = "d23441a48e516b6c34aea4fa41551a30e30af803"

PYPROJECT = """\
[build-system]
requires = ["hatchling==1.32.4"]
build-backend = "hatchling.build"

[project]
name = "fixture"
version = "0.0.0"
dependencies = [
  "PyYAML==6.0.3",
  "jsonschema==4.26.0",
]

[project.optional-dependencies]
dev = [
  "pytest==9.0.2",
]
"""

RUNTIME_LOCK = "hatchling==1.32.4\nPyYAML==6.0.3\njsonschema==4.26.0\n"
DEV_LOCK = "-r requirements.lock\npytest==9.0.2\n"


def _load_audit(repo_root: Path):
    path = repo_root / "scripts" / "audit_supply_chain.py"
    spec = importlib.util.spec_from_file_location("test_supply_chain_audit", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dependency_repo(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch,
    *,
    pyproject: str = PYPROJECT,
    runtime_lock: str = RUNTIME_LOCK,
    dev_lock: str = DEV_LOCK,
):
    """A minimal repository whose metadata and locks can be drifted independently."""
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    (tmp_path / "requirements.lock").write_text(runtime_lock, encoding="utf-8")
    (tmp_path / "requirements-dev.lock").write_text(dev_lock, encoding="utf-8")
    module = _load_audit(repo_root)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    return module


def _ci_workflow(
    *,
    cache_paths: tuple[str, ...] = ("requirements.lock", "requirements-dev.lock"),
    install: str = (
        "python -m pip install --require-hashes --only-binary=:all: "
        "--requirement requirements-dev.lock"
    ),
    dev_check: str = "          python -m pip check\n",
    wheel_check: str = "          .work/wheel-venv/bin/python -m pip check\n",
) -> str:
    cached = "".join(f"                  {name}\n" for name in cache_paths)
    return (
        "name: ci\n"
        "on: [push]\n"
        "jobs:\n"
        "  quality:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6\n"
        "        with:\n"
        "          python-version: '3.12'\n"
        "          cache: pip\n"
        "          cache-dependency-path: |\n"
        f"{cached}"
        "      - name: Install hash-verified development dependencies\n"
        "        run: |\n"
        f"          {install}\n"
        "      - name: Verify installed dependency consistency\n"
        "        run: |\n"
        f"{dev_check}"
        "      - name: Smoke test the installed wheel\n"
        "        run: |\n"
        "          .work/wheel-venv/bin/python -m pip install --require-hashes "
        "--only-binary=:all: --requirement requirements.lock\n"
        f"{wheel_check}"
    )


def _lock_only_workflow(cache_path: str) -> str:
    """A production workflow that installs the runtime lock alone."""
    return (
        "name: publish\n"
        "on: [workflow_dispatch]\n"
        "jobs:\n"
        "  publish:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6\n"
        "        with:\n"
        "          python-version: '3.12'\n"
        "          cache: pip\n"
        f"          cache-dependency-path: {cache_path}\n"
        "      - name: Install hash-verified runtime dependencies\n"
        "        run: |\n"
        "          python -m pip install --require-hashes --only-binary=:all: --requirement requirements.lock\n"
    )


def _workflow_repo(repo_root: Path, tmp_path: Path, monkeypatch, **workflows: str):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "requirements.lock").write_text(RUNTIME_LOCK, encoding="utf-8")
    (tmp_path / "requirements-dev.lock").write_text(DEV_LOCK, encoding="utf-8")
    directory = tmp_path / ".github" / "workflows"
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in workflows.items():
        (directory / f"{name}.yml").write_text(text, encoding="utf-8")
    module = _load_audit(repo_root)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    return module


def _audit_fixture(repo_root: Path, tmp_path: Path, monkeypatch, name: str, text: str) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    (workflows / name).write_text(text, encoding="utf-8")
    module = _load_audit(repo_root)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    module._audit_workflow_actions()


def test_repository_workflows_pass_the_structural_audit(repo_root: Path) -> None:
    module = _load_audit(repo_root)
    module._audit_workflow_actions()


@pytest.mark.parametrize("suffix", [".yml", ".yaml"])
@pytest.mark.parametrize(
    ("name", "reference"),
    [
        ("plain step", "main"),
        ("tagged action", "v6"),
        ("branch ref", "release/v2"),
    ],
)
def test_movable_reference_fails_from_a_named_step(
    repo_root: Path, tmp_path: Path, monkeypatch, suffix: str, name: str, reference: str
) -> None:
    text = (
        "name: fixture\n"
        "on: [push]\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        f"      - name: {name}\n"
        f"        uses: actions/checkout@{reference}\n"
    )

    with pytest.raises(SystemExit, match="movable action ref"):
        _audit_fixture(repo_root, tmp_path, monkeypatch, f"fixture{suffix}", text)


def test_movable_job_reusable_workflow_fails(repo_root: Path, tmp_path: Path, monkeypatch) -> None:
    text = "jobs:\n  call:\n    uses: example/example/.github/workflows/reusable.yml@main\n"

    with pytest.raises(SystemExit, match="movable action ref"):
        _audit_fixture(repo_root, tmp_path, monkeypatch, "fixture.yml", text)


def test_external_reference_without_ref_fails(repo_root: Path, tmp_path: Path, monkeypatch) -> None:
    text = "jobs:\n  test:\n    steps:\n      - uses: actions/checkout\n"

    with pytest.raises(SystemExit, match="lacks an immutable ref"):
        _audit_fixture(repo_root, tmp_path, monkeypatch, "fixture.yml", text)


def test_unpinned_container_action_fails(repo_root: Path, tmp_path: Path, monkeypatch) -> None:
    text = "jobs:\n  test:\n    steps:\n      - uses: docker://alpine:3.20\n"

    with pytest.raises(SystemExit, match="unpinned container action"):
        _audit_fixture(repo_root, tmp_path, monkeypatch, "fixture.yml", text)


def test_pinned_references_pass_regardless_of_layout(
    repo_root: Path, tmp_path: Path, monkeypatch
) -> None:
    text = (
        "# comment before the mapping\n"
        "jobs:\n"
        "  test:\n"
        "    steps:\n"
        "      - run: echo fixture\n"
        "      - name: pinned\n"
        f"        uses:  actions/checkout@{FULL_SHA}   # trailing comment\n"
        "  call:\n"
        f"    uses: ./.github/workflows/ci.yml\n"
        "  digest:\n"
        f"    uses: docker://alpine@sha256:{'0' * 64}\n"
    )
    other = f"jobs:\n  test:\n    steps:\n      - uses: actions/setup-python@{FULL_SHA}\n"

    _audit_fixture(repo_root, tmp_path, monkeypatch, "fixture.yml", text)
    _audit_fixture(repo_root, tmp_path, monkeypatch, "second.yaml", other)


def test_malformed_workflow_fails_the_audit(repo_root: Path, tmp_path: Path, monkeypatch) -> None:
    with pytest.raises(SystemExit, match="cannot parse workflow"):
        _audit_fixture(repo_root, tmp_path, monkeypatch, "fixture.yml", "jobs: [unclosed\n")


def test_workflow_without_jobs_fails_the_audit(
    repo_root: Path, tmp_path: Path, monkeypatch
) -> None:
    with pytest.raises(SystemExit, match="declares no jobs"):
        _audit_fixture(repo_root, tmp_path, monkeypatch, "fixture.yml", "name: fixture\n")


def test_temporary_lock_probe_must_not_remain(repo_root: Path, tmp_path: Path, monkeypatch) -> None:
    with pytest.raises(SystemExit, match="lock probe must not remain"):
        _audit_fixture(repo_root, tmp_path, monkeypatch, "lock-probe.yml", "jobs: {}\n")


def test_repository_dependency_metadata_matches_the_locks(repo_root: Path) -> None:
    module = _load_audit(repo_root)

    module._audit_dependency_metadata(repo_root)


def test_repository_locks_pass_the_structural_audit(repo_root: Path) -> None:
    module = _load_audit(repo_root)

    module._audit_lock(repo_root / "requirements.lock")
    module._audit_lock(repo_root / "requirements-dev.lock")


def test_platform_marker_entry_is_audited_and_pinned(
    repo_root: Path, tmp_path: Path, monkeypatch
) -> None:
    digest = "a" * 64
    module = _dependency_repo(
        repo_root,
        tmp_path,
        monkeypatch,
        dev_lock=(
            "-r requirements.lock\n"
            'colorama==0.4.6 ; sys_platform == "win32" \\\n'
            f"    --hash=sha256:{digest}\n"
            "pytest==9.0.2 \\\n"
            f"    --hash=sha256:{digest}\n"
        ),
    )

    module._audit_lock(tmp_path / "requirements-dev.lock")
    pins = module._lock_pins(tmp_path, "requirements-dev.lock")

    assert pins["colorama"] == "0.4.6"
    assert pins["pytest"] == "9.0.2"


def test_platform_marker_entry_without_a_hash_fails_the_audit(
    repo_root: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _dependency_repo(
        repo_root,
        tmp_path,
        monkeypatch,
        dev_lock='-r requirements.lock\ncolorama==0.4.6 ; sys_platform == "win32"\n',
    )

    with pytest.raises(SystemExit, match="unhashed dependency"):
        module._audit_lock(tmp_path / "requirements-dev.lock")


def test_metadata_version_drift_fails_the_audit(
    repo_root: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _dependency_repo(
        repo_root,
        tmp_path,
        monkeypatch,
        pyproject=PYPROJECT.replace("PyYAML==6.0.3", "PyYAML==6.0.2"),
    )

    with pytest.raises(
        SystemExit,
        match=r"PyYAML is pinned 6\.0\.2 in pyproject\.toml but 6\.0\.3 in requirements\.lock",
    ):
        module._audit_dependency_metadata(tmp_path)


def test_lock_only_version_drift_fails_the_audit(
    repo_root: Path, tmp_path: Path, monkeypatch
) -> None:
    # Changing a single side of the pair must be rejected: the metadata is the
    # declared contract and the lock is what CI installs.
    module = _dependency_repo(
        repo_root,
        tmp_path,
        monkeypatch,
        runtime_lock=RUNTIME_LOCK.replace("PyYAML==6.0.3", "PyYAML==6.0.4"),
    )

    with pytest.raises(
        SystemExit,
        match=r"PyYAML is pinned 6\.0\.3 in pyproject\.toml but 6\.0\.4 in requirements\.lock",
    ):
        module._audit_dependency_metadata(tmp_path)


def test_dependency_names_are_compared_after_normalization(
    repo_root: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _dependency_repo(
        repo_root,
        tmp_path,
        monkeypatch,
        runtime_lock=RUNTIME_LOCK.replace("PyYAML==6.0.3", "pyyaml==6.0.3"),
    )

    module._audit_dependency_metadata(tmp_path)


def test_development_extra_is_compared_against_the_development_lock(
    repo_root: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _dependency_repo(
        repo_root,
        tmp_path,
        monkeypatch,
        dev_lock=DEV_LOCK.replace("pytest==9.0.2", "pytest==9.1.0"),
    )

    with pytest.raises(
        SystemExit,
        match=r"pytest is pinned 9\.0\.2 in pyproject\.toml but 9\.1\.0 in requirements-dev\.lock",
    ):
        module._audit_dependency_metadata(tmp_path)


def test_dependency_missing_from_the_lock_fails_the_audit(
    repo_root: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _dependency_repo(
        repo_root,
        tmp_path,
        monkeypatch,
        runtime_lock="hatchling==1.32.4\nPyYAML==6.0.3\n",
    )

    with pytest.raises(SystemExit, match=r"jsonschema is missing from requirements\.lock"):
        module._audit_dependency_metadata(tmp_path)


def test_unpinned_metadata_requirement_fails_the_audit(
    repo_root: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _dependency_repo(
        repo_root,
        tmp_path,
        monkeypatch,
        pyproject=PYPROJECT.replace('"PyYAML==6.0.3"', '"PyYAML>=6.0"'),
    )

    with pytest.raises(SystemExit, match="must pin an exact '==' version"):
        module._audit_dependency_metadata(tmp_path)


def test_dependency_group_without_a_lock_mapping_fails_the_audit(
    repo_root: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _dependency_repo(
        repo_root,
        tmp_path,
        monkeypatch,
        pyproject=PYPROJECT.replace(
            "[project.optional-dependencies]\n",
            '[project.optional-dependencies]\ndocs = ["sphinx==8.2.3"]\n',
        ),
    )

    with pytest.raises(SystemExit, match="has no lock file mapping"):
        module._audit_dependency_metadata(tmp_path)


def test_repository_install_environment_passes_the_audit(repo_root: Path) -> None:
    module = _load_audit(repo_root)

    module._audit_install_environment(repo_root)


def test_development_cache_key_must_cover_every_installed_lock(
    repo_root: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _workflow_repo(
        repo_root, tmp_path, monkeypatch, ci=_ci_workflow(cache_paths=("requirements-dev.lock",))
    )

    with pytest.raises(
        SystemExit,
        match=r"CI job 'quality' in ci\.yml installs requirements\.lock without covering it",
    ):
        module._audit_install_environment(tmp_path)


def test_runtime_lock_change_must_change_the_development_cache_key(
    repo_root: Path, tmp_path: Path, monkeypatch
) -> None:
    """A key covering the dev lock alone would be reused for a changed runtime lock.

    requirements-dev.lock includes requirements.lock through a ``-r`` line, so the
    runtime lock is installed by the dev job even though it is a separate file.
    """
    module = _workflow_repo(repo_root, tmp_path, monkeypatch, ci=_ci_workflow())

    module._audit_install_environment(tmp_path)


def test_development_cache_key_must_include_the_development_lock(
    repo_root: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _workflow_repo(
        repo_root, tmp_path, monkeypatch, ci=_ci_workflow(cache_paths=("requirements.lock",))
    )

    with pytest.raises(SystemExit, match=r"installs requirements-dev\.lock without covering it"):
        module._audit_install_environment(tmp_path)


def test_ci_must_run_pip_check_in_the_development_environment(
    repo_root: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _workflow_repo(repo_root, tmp_path, monkeypatch, ci=_ci_workflow(dev_check=""))

    with pytest.raises(
        SystemExit,
        match=r"development environment without running 'python -m pip check'",
    ):
        module._audit_install_environment(tmp_path)


def test_ci_must_run_pip_check_in_the_standalone_wheel_environment(
    repo_root: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _workflow_repo(repo_root, tmp_path, monkeypatch, ci=_ci_workflow(wheel_check=""))

    with pytest.raises(
        SystemExit,
        match=r"standalone wheel environment without running '\.work/wheel-venv/bin/python -m pip check'",
    ):
        module._audit_install_environment(tmp_path)


def test_runtime_workflow_installs_must_cover_their_lock(
    repo_root: Path, tmp_path: Path, monkeypatch
) -> None:
    covered = _workflow_repo(
        repo_root,
        tmp_path,
        monkeypatch,
        publish=_lock_only_workflow("requirements.lock"),
    )
    covered._audit_install_environment(tmp_path)

    drifted = _workflow_repo(
        repo_root,
        tmp_path,
        monkeypatch,
        publish=_lock_only_workflow("requirements-dev.lock"),
    )
    with pytest.raises(
        SystemExit,
        match=r"CI job 'publish' in publish\.yml installs requirements\.lock without covering it",
    ):
        drifted._audit_install_environment(tmp_path)


def test_workflow_without_jobs_fails_the_install_audit(
    repo_root: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _workflow_repo(repo_root, tmp_path, monkeypatch, ci="name: ci\n")

    with pytest.raises(SystemExit, match="declares no jobs"):
        module._audit_install_environment(tmp_path)


CI_PIP_CHECKS = (
    ("quality", "python -m pip check"),
    ("quality", ".work/wheel-venv/bin/python -m pip check"),
    ("python-compat", "python -m pip check"),
    ("windows-platform", "python -m pip check"),
    ("deterministic-generation", "python -m pip check"),
    ("stable-mihomo", "python -m pip check"),
)


def _remove_command(text: str, job_name: str, command: str) -> str:
    """Replace one run command in the named job with a no-op, leaving the rest untouched."""
    lines = text.splitlines(keepends=True)
    start = next(index for index, line in enumerate(lines) if line.startswith(f"  {job_name}:"))
    end = next(
        (index for index in range(start + 1, len(lines)) if re.match(r"^  \S", lines[index])),
        len(lines),
    )
    for index in range(start, end):
        stripped = lines[index].strip()
        newline = "\n" if lines[index].endswith("\n") else ""
        if stripped == f"run: {command}":
            lines[index] = lines[index].replace(f"run: {command}", 'run: "true"')
            return "".join(lines)
        if stripped == command:
            indentation = len(lines[index]) - len(lines[index].lstrip())
            lines[index] = " " * indentation + "true" + newline
            return "".join(lines)
    raise AssertionError(f"{job_name} does not run {command!r}")


def _ci_only_repository(repo_root: Path, tmp_path: Path, monkeypatch, ci_text: str):
    """A repository whose ci.yml is the real workflow with one property changed."""
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "requirements.lock").write_text(RUNTIME_LOCK, encoding="utf-8")
    (tmp_path / "requirements-dev.lock").write_text(DEV_LOCK, encoding="utf-8")
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    (workflows / "ci.yml").write_text(ci_text, encoding="utf-8")
    module = _load_audit(repo_root)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    return module


def test_the_repository_ci_workflow_passes_the_install_audit(
    repo_root: Path, tmp_path: Path, monkeypatch
) -> None:
    ci = (repo_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    module = _ci_only_repository(repo_root, tmp_path, monkeypatch, ci)

    module._audit_install_environment(tmp_path)


@pytest.mark.parametrize(("job_name", "command"), CI_PIP_CHECKS)
def test_removing_any_single_pip_check_fails_the_audit(
    repo_root: Path, tmp_path: Path, monkeypatch, job_name: str, command: str
) -> None:
    """Each job owns its environments: one deleted check must fail on that job alone."""
    ci = (repo_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    module = _ci_only_repository(
        repo_root, tmp_path, monkeypatch, _remove_command(ci, job_name, command)
    )

    with pytest.raises(
        SystemExit,
        match=rf"CI job '{re.escape(job_name)}' in ci\.yml installs the [a-z ]+ environment "
        r"without running",
    ):
        module._audit_install_environment(tmp_path)


def test_a_check_in_another_job_does_not_cover_the_missing_one(
    repo_root: Path, tmp_path: Path, monkeypatch
) -> None:
    """A workflow-wide command set would accept this; the per-job rule must not."""
    ci = (repo_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    text = _remove_command(ci, "python-compat", "python -m pip check")
    text = text.replace(
        "      - name: Confirm embedded Routing V2 drift gate passed\n",
        "      - name: Redundant consistency check elsewhere\n"
        "        run: python -m pip check\n"
        "      - name: Confirm embedded Routing V2 drift gate passed\n",
        1,
    )
    module = _ci_only_repository(repo_root, tmp_path, monkeypatch, text)

    with pytest.raises(SystemExit, match=r"CI job 'python-compat' in ci\.yml installs the"):
        module._audit_install_environment(tmp_path)
