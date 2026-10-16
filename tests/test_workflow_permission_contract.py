"""Reusable-workflow callers must grant at least the permissions the callee needs.

A reusable workflow's effective ``GITHUB_TOKEN`` permissions are the intersection
of the caller job's granted permissions and the callee's declared permissions.
A caller that grants less than the callee declares silently downgrades the
callee to ``none`` for that scope, which fails at runtime instead of at review
time. These tests check the effective permission semantics rather than asserting
that a literal string appears somewhere in the file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
CI_WORKFLOW = "./.github/workflows/ci.yml"
_ACCESS_LEVELS = {"none": 0, "read": 1, "write": 2}


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _declared_permissions(document: dict[str, Any]) -> dict[str, str]:
    permissions = document.get("permissions")
    if not isinstance(permissions, dict):
        return {}
    return {str(scope): str(level) for scope, level in permissions.items()}


def _missing_permissions(
    caller: dict[str, str],
    required: dict[str, str],
) -> list[str]:
    """Return the scopes where the caller grants less than the callee declares."""
    missing: list[str] = []
    for scope, level in required.items():
        granted = caller.get(scope, "none")
        if _ACCESS_LEVELS.get(granted, 0) < _ACCESS_LEVELS.get(level, 0):
            missing.append(scope)
    return sorted(missing)


def _reusable_call_jobs(document: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Effective permissions for every job that calls the shared CI workflow."""
    workflow_permissions = _declared_permissions(document)
    effective: dict[str, dict[str, str]] = {}
    for name, job in (document.get("jobs") or {}).items():
        if not isinstance(job, dict) or job.get("uses") != CI_WORKFLOW:
            continue
        effective[str(name)] = (
            _declared_permissions(job) if "permissions" in job else workflow_permissions
        )
    return effective


def _callers() -> list[tuple[str, str, dict[str, str]]]:
    callers: list[tuple[str, str, dict[str, str]]] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        document = _load(path.name)
        if not isinstance(document, dict):
            continue
        for job_name, permissions in _reusable_call_jobs(document).items():
            callers.append((path.name, job_name, permissions))
    return callers


def test_shared_ci_workflow_declares_its_required_permissions() -> None:
    required = _declared_permissions(_load("ci.yml"))

    assert required.get("actions") == "read"
    assert required.get("contents") == "read"


def test_every_ci_caller_grants_the_declared_permissions() -> None:
    required = _declared_permissions(_load("ci.yml"))
    callers = _callers()

    assert callers, "expected at least one workflow to reuse the shared CI workflow"
    for workflow, job_name, permissions in callers:
        missing = _missing_permissions(permissions, required)
        assert not missing, (
            f"{workflow} job {job_name!r} calls {CI_WORKFLOW} without granting "
            f"required permission scopes: {missing}"
        )


@pytest.mark.parametrize("workflow", ["rollback.yml", "retention.yml"])
def test_rollback_and_retention_grant_actions_read(workflow: str) -> None:
    document = _load(workflow)
    jobs = _reusable_call_jobs(document)

    assert jobs, f"{workflow} no longer reuses {CI_WORKFLOW}"
    for permissions in jobs.values():
        assert permissions.get("actions") == "read"


def test_windows_platform_job_is_part_of_the_validated_gate() -> None:
    ci = _load("ci.yml")
    jobs = ci.get("jobs") or {}

    windows = jobs.get("windows-platform")
    assert windows is not None
    assert windows["runs-on"] == "windows-latest"
    steps = " ".join(str(step.get("run", "")) for step in windows["steps"])
    # Every command judges the gate's own environment. The image installs its
    # tooling into the interpreter setup-python exposes, and pipx there requires
    # a newer packaging than the pinned lock, so the shared interpreter would
    # fail the consistency check for a conflict this project does not own.
    assert ".work/dev-venv/Scripts/python.exe scripts/run_project_checks.py typecheck" in steps
    assert ".work/dev-venv/Scripts/python.exe -m pytest tests/test_mihomo_probe_runtime.py" in steps
    assert "python -m venv .work/dev-venv" in steps

    validated = jobs["validated"]
    assert "windows-platform" in validated["needs"]
    assert "needs.windows-platform.result == 'success'" in validated["if"]


def test_windows_platform_job_does_not_open_write_permissions() -> None:
    ci = _load("ci.yml")
    windows = ci.get("jobs", {}).get("windows-platform")

    assert "permissions" not in windows
    assert _declared_permissions(ci) == {"actions": "read", "contents": "read"}


def test_caller_missing_a_required_scope_is_rejected() -> None:
    required = _declared_permissions(_load("ci.yml"))

    # Negative fixture: the pre-fix rollback/retention shape granted contents only.
    missing = _missing_permissions({"contents": "read"}, required)

    assert missing == ["actions"]


def test_caller_write_scope_satisfies_read_requirement() -> None:
    required = _declared_permissions(_load("ci.yml"))

    assert _missing_permissions({"actions": "write", "contents": "read"}, required) == []
