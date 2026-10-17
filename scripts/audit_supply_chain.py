#!/usr/bin/env python3
"""Fail closed when P50 validation or dependency supply-chain gates regress."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SHA_REF = re.compile(r"^[0-9a-f]{40}$")
DOCKER_DIGEST = re.compile(r"^docker://[^@\s]+@sha256:[0-9a-f]{64}$")
LOCK_REQUIREMENT = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)==(?P<version>\S+)$")
METADATA_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9._-]+)(?:\[[^\]]*\])?==(?P<version>[^\s;]+)$"
)
LOCK_INCLUDE = re.compile(r"^-r\s+(?P<path>\S+)$")
PIP_COMMAND = re.compile(r"^(?P<interpreter>\S+)\s+-m\s+pip\s+(?P<action>install|check)\b")
PYPROJECT = "pyproject.toml"
# Which lock installs which dependency group. ``dev`` mirrors the single optional
# dependency group in ``pyproject.toml``; a new group without a mapping fails the
# audit instead of silently escaping the gate.
DEPENDENCY_GROUPS = {
    "build-system": "requirements.lock",
    "runtime": "requirements.lock",
    "dev": "requirements-dev.lock",
}


def _logical_requirements(path: Path) -> list[str]:
    logical: list[str] = []
    current: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        current.append(stripped.removesuffix("\\").strip())
        if not stripped.endswith("\\"):
            logical.append(" ".join(current))
            current = []
    if current:
        raise SystemExit(f"supply-chain audit: unterminated requirement in {path.name}")
    return logical


def _audit_lock(path: Path) -> None:
    requirements = _logical_requirements(path)
    if not requirements:
        raise SystemExit(f"supply-chain audit: empty lock file {path.name}")
    for requirement in requirements:
        if requirement.startswith("-r "):
            continue
        head = requirement.split()[0]
        if "==" not in head:
            raise SystemExit(f"supply-chain audit: unpinned dependency in {path.name}: {head}")
        hashes = [token for token in requirement.split() if token.startswith("--hash=sha256:")]
        if not hashes:
            raise SystemExit(f"supply-chain audit: unhashed dependency in {path.name}: {head}")
        for token in hashes:
            digest = token.removeprefix("--hash=sha256:")
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise SystemExit(f"supply-chain audit: invalid SHA-256 hash in {path.name}: {head}")


def _audit_workflow_reference(path: Path, label: str, reference: Any) -> None:
    if reference is None:
        return
    if not isinstance(reference, str) or not reference.strip():
        raise SystemExit(f"supply-chain audit: invalid uses reference in {path.name} ({label})")
    target = reference.strip()
    if target.startswith("./"):
        # Local workflow in this repository: reviewed in the same commit and
        # therefore not bound to an immutable remote ref.
        return
    if target.startswith("docker://"):
        if not DOCKER_DIGEST.fullmatch(target):
            raise SystemExit(
                f"supply-chain audit: unpinned container action in {path.name} ({label}): {target}"
            )
        return
    if "@" not in target:
        raise SystemExit(
            f"supply-chain audit: external reference lacks an immutable ref in {path.name} "
            f"({label}): {target}"
        )
    ref = target.rsplit("@", 1)[1]
    if not SHA_REF.fullmatch(ref):
        raise SystemExit(
            f"supply-chain audit: movable action ref in {path.name} ({label}): {target}"
        )


def _audit_workflow(path: Path) -> None:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise SystemExit(f"supply-chain audit: cannot parse workflow {path.name}") from exc
    if not isinstance(document, dict):
        raise SystemExit(f"supply-chain audit: workflow {path.name} is not a YAML mapping")
    jobs = document.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        raise SystemExit(f"supply-chain audit: workflow {path.name} declares no jobs")
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            raise SystemExit(
                f"supply-chain audit: job {job_name!r} in {path.name} is not a mapping"
            )
        _audit_workflow_reference(path, f"job {job_name!r}", job.get("uses"))
        steps = job.get("steps")
        if steps is None:
            continue
        if not isinstance(steps, list):
            raise SystemExit(
                f"supply-chain audit: steps of job {job_name!r} in {path.name} must be a list"
            )
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                raise SystemExit(
                    f"supply-chain audit: step {index} of job {job_name!r} in {path.name} "
                    "is not a mapping"
                )
            _audit_workflow_reference(path, f"step {index} of job {job_name!r}", step.get("uses"))


def _audit_workflow_actions() -> None:
    # Both extensions are parsed structurally: a named step, a reusable-workflow
    # job and any other YAML layout must reach the same decision as the plain
    # ``- uses:`` list form. A workflow that cannot be parsed fails the audit
    # instead of passing unreviewed.
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    workflows += sorted((ROOT / ".github" / "workflows").glob("*.yaml"))
    for path in workflows:
        if path.name == "lock-probe.yml":
            raise SystemExit("supply-chain audit: temporary P50 lock probe must not remain")
        _audit_workflow(path)


def _normalized_name(name: str) -> str:
    """PEP 503 normalization, so ``PyYAML`` and ``pyyaml`` compare equal."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _lock_closure(root: Path, name: str) -> set[str]:
    """The lock file plus every lock it includes through ``-r``."""
    seen: set[str] = set()
    pending = [name]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        try:
            requirements = _logical_requirements(root / current)
        except OSError as exc:
            raise SystemExit(f"supply-chain audit: cannot read lock {current}") from exc
        for requirement in requirements:
            include = LOCK_INCLUDE.fullmatch(requirement)
            if include is not None:
                pending.append(include.group("path"))
    return seen


def _lock_pins(root: Path, name: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for lock in sorted(_lock_closure(root, name)):
        for requirement in _logical_requirements(root / lock):
            head = requirement.split()[0]
            match = LOCK_REQUIREMENT.fullmatch(head)
            if match is None:
                continue
            pins[_normalized_name(match.group("name"))] = match.group("version")
    return pins


def _pinned_metadata_requirement(raw: Any, group: str) -> tuple[str, str]:
    if not isinstance(raw, str):
        raise SystemExit(f"supply-chain audit: {group} dependency declaration must be a string")
    match = METADATA_REQUIREMENT.fullmatch(raw.strip())
    if match is None:
        raise SystemExit(
            f"supply-chain audit: {group} dependency {raw!r} must pin an exact '==' version"
        )
    return match.group("name"), match.group("version")


def _metadata_dependency_groups(path: Path) -> dict[str, list[tuple[str, str]]]:
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise SystemExit(f"supply-chain audit: cannot read {path.name}") from exc

    build = document.get("build-system")
    project = document.get("project")
    if not isinstance(build, dict) or not isinstance(project, dict):
        raise SystemExit(f"supply-chain audit: {path.name} must declare build-system and project")

    groups: dict[str, list[tuple[str, str]]] = {
        "build-system": [
            _pinned_metadata_requirement(item, "build-system") for item in build.get("requires", [])
        ],
        "runtime": [
            _pinned_metadata_requirement(item, "runtime")
            for item in project.get("dependencies", [])
        ],
    }
    extras = project.get("optional-dependencies", {})
    if not isinstance(extras, dict):
        raise SystemExit(f"supply-chain audit: {path.name} optional-dependencies must be a table")
    for extra, requirements in extras.items():
        if not isinstance(requirements, list):
            raise SystemExit(
                f"supply-chain audit: {path.name} optional dependency group {extra!r} "
                "must be a list"
            )
        groups[str(extra)] = [
            _pinned_metadata_requirement(item, str(extra)) for item in requirements
        ]
    return groups


def _audit_dependency_metadata(root: Path) -> None:
    """Every declared requirement must be pinned identically in the lock that installs it."""
    pins: dict[str, dict[str, str]] = {}
    for group, requirements in _metadata_dependency_groups(root / PYPROJECT).items():
        lock_name = DEPENDENCY_GROUPS.get(group)
        if lock_name is None:
            raise SystemExit(
                f"supply-chain audit: dependency group {group!r} has no lock file mapping in "
                "scripts/audit_supply_chain.py"
            )
        if lock_name not in pins:
            pins[lock_name] = _lock_pins(root, lock_name)
        for name, version in requirements:
            locked = pins[lock_name].get(_normalized_name(name))
            if locked is None:
                raise SystemExit(
                    f"supply-chain audit: {group} dependency {name} is missing from {lock_name}"
                )
            if locked != version:
                raise SystemExit(
                    f"supply-chain audit: {group} dependency {name} is pinned {version} in "
                    f"{PYPROJECT} but {locked} in {lock_name}"
                )


def _installed_lock_names(run: str) -> set[str]:
    return set(re.findall(r"--requirement\s+(?P<path>\S+)", run))


def _workflow_paths(root: Path) -> list[Path]:
    workflows = sorted((root / ".github" / "workflows").glob("*.yml"))
    workflows += sorted((root / ".github" / "workflows").glob("*.yaml"))
    return workflows


def _job_pip_usage(job: Any) -> dict[str, dict[str, Any]]:
    """Interpreter -> what the job's own run steps installed and checked with it."""
    usage: dict[str, dict[str, Any]] = {}
    steps = job.get("steps") if isinstance(job, dict) else None
    if not isinstance(steps, list):
        return usage
    for step in steps:
        run = step.get("run") if isinstance(step, dict) else None
        if not isinstance(run, str):
            continue
        for line in run.splitlines():
            match = PIP_COMMAND.match(line.strip())
            if match is None:
                continue
            entry = usage.setdefault(
                match.group("interpreter"), {"install": False, "check": False, "locks": set()}
            )
            if match.group("action") == "install":
                entry["install"] = True
                entry["locks"] |= _installed_lock_names(line)
            else:
                entry["check"] = True
                # A check that cannot fail proves nothing: ``continue-on-error``
                # and an ``if: false`` guard both turn the step into a no-op.
                if step.get("continue-on-error") in (True, "true"):
                    entry["check_can_fail"] = False
                condition = step.get("if")
                if condition is False or (
                    isinstance(condition, str) and condition.strip() in ("false", "${{ false }}")
                ):
                    entry["check_can_fail"] = False
    return usage


def _environment_label(interpreter: str, locks: Any) -> str:
    """Name an environment the way the workflow's own step names describe it."""
    if "wheel-venv" in interpreter:
        return "standalone wheel"
    if any(Path(lock).name == "requirements-dev.lock" for lock in locks):
        return "development"
    return "runtime"


def _audit_pip_checks(name: str, jobs: dict[str, Any]) -> None:
    """Every interpreter that installed dependencies in a job must check them in that job.

    Judging the command set of the whole workflow would let one job's check stand in
    for another job's install, so deleting the check from any single job — or
    substituting a different interpreter — would stay invisible to this audit.

    Every workflow is judged, not only ``ci.yml``: the production workflows install
    the runtime lock in their own jobs, and a check there must not be satisfied by
    the validation workflow that ran earlier. A job that owns its install owns the
    consistency check.
    """
    for job_name, job in jobs.items():
        for interpreter, usage in sorted(_job_pip_usage(job).items()):
            if not usage["install"]:
                continue
            if usage["check"] and usage.get("check_can_fail", True):
                continue
            environment = _environment_label(interpreter, usage["locks"])
            raise SystemExit(
                f"supply-chain audit: job {job_name!r} in {name} installs the {environment} "
                f"environment without a failing {interpreter + ' -m pip check'!r} step"
            )


def _audit_install_environment(root: Path) -> None:
    """Require install consistency checks and pip cache keys covering installed locks."""
    for path in _workflow_paths(root):
        try:
            text = path.read_text(encoding="utf-8")
            document = yaml.safe_load(text)
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise SystemExit(f"supply-chain audit: cannot parse workflow {path.name}") from exc
        jobs = document.get("jobs") if isinstance(document, dict) else None
        if not isinstance(jobs, dict):
            raise SystemExit(f"supply-chain audit: workflow {path.name} declares no jobs")
        _audit_pip_checks(path.name, jobs)
        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            steps = job.get("steps")
            if not isinstance(steps, list):
                continue
            installed: set[str] = set()
            cache_paths: set[str] = set()
            for step in steps:
                if not isinstance(step, dict):
                    continue
                run = step.get("run")
                if isinstance(run, str):
                    for lock in _installed_lock_names(run):
                        installed |= _lock_closure(root, lock)
                uses = step.get("uses")
                if not isinstance(uses, str) or not uses.startswith("actions/setup-python@"):
                    continue
                with_ = step.get("with")
                declared = with_.get("cache-dependency-path") if isinstance(with_, dict) else None
                if isinstance(declared, str):
                    cache_paths |= {
                        Path(entry.strip()).name
                        for entry in declared.replace(",", "\n").splitlines()
                        if entry.strip()
                    }
            missing = sorted(name for name in installed if Path(name).name not in cache_paths)
            if missing:
                raise SystemExit(
                    f"supply-chain audit: CI job {job_name!r} in {path.name} installs "
                    f"{', '.join(missing)} without covering it in the pip cache key"
                )


def main() -> int:
    _audit_lock(ROOT / "requirements.lock")
    _audit_lock(ROOT / "requirements-dev.lock")
    _audit_dependency_metadata(ROOT)
    _audit_install_environment(ROOT)
    _audit_workflow_actions()

    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for token in (
        "workflow_call:",
        "validated_sha:",
        "--require-hashes",
        "--only-binary=:all:",
        "--no-build-isolation",
        "--cov-fail-under=75",
        "scripts/run_project_checks.py audit",
        "scripts/run_project_checks.py typecheck",
        "Validated SHA",
    ):
        if token not in ci:
            raise SystemExit(f"supply-chain audit: reusable CI validation gate missing {token}")

    publish = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    for token in (
        "uses: ./.github/workflows/ci.yml",
        "needs.validate.outputs.validated_sha == github.sha",
        "ref: ${{ needs.validate.outputs.validated_sha }}",
        'test "$VALIDATED_SHA" = "$GITHUB_SHA"',
        'test "$(git rev-parse HEAD)" = "$VALIDATED_SHA"',
        "CLASH_RELAY_VALIDATED_SHA: ${{ needs.validate.outputs.validated_sha }}",
    ):
        if token not in publish:
            raise SystemExit(f"supply-chain audit: publication SHA binding missing {token}")

    entrypoint = (ROOT / "scripts" / "run_production_release.py").read_text(encoding="utf-8")
    for token in ("GITHUB_ACTIONS", "GITHUB_SHA", "CLASH_RELAY_VALIDATED_SHA"):
        if token not in entrypoint:
            raise SystemExit(f"supply-chain audit: publication entrypoint missing {token}")

    print("supply-chain audit: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
