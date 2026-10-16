"""CI report retention: a short-lived whitelist of synthetic test reports.

The contract has five parts: every report a job produces is archived, the archive
path stays a specific ``.work/*.xml`` file rather than the whole ``.work``
directory, the declaration actually matches under the pinned action's glob rules,
the artifact survives a failing step with an identical declaration, and the
retention window is short. Each rule is exercised against the real workflow and
against a fixture that changes exactly one thing, so a regression fails on the
property it broke.

The matching rule is not decoration. ``actions/upload-artifact`` builds its
globber with ``excludeHiddenFiles: !includeHiddenFiles`` (``src/shared/search.ts``
at the pinned SHA), so every report under this repository's hidden ``.work``
directory needs ``include-hidden-files: true`` before a wildcard declaration can
match anything at all.
"""

from __future__ import annotations

import copy
import fnmatch
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

import pytest
import yaml

UPLOAD_ARTIFACT = "actions/upload-artifact"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
PRODUCED_REPORT = re.compile(r"--(?:junitxml=|cov-report=xml:)(?P<path>\S+)")
EXPRESSION = re.compile(r"\$\{\{\s*([^{}]*?)\s*\}\}")
SHORT_RETENTION_DAYS = 7
REPORT_EXTENSION = ".xml"
MAGIC = "*?["
MATRIX: Mapping[str, str] = {"matrix.python": "3.13", "matrix.tag": "v1.19.3"}
ATTEMPTS: Sequence[str] = ("1", "2", "3")

# Files the quality and integration jobs can plausibly leave in .work/, next to
# secrets, generated configuration, the downloaded core binary, and the wheel
# virtualenv. Only the reports may be inside the archive scope.
SYNTHETIC_REPORTS = (
    ".work/unit-3.12.xml",
    ".work/coverage-3.12.xml",
    ".work/compat-3.13.xml",
    ".work/unit-windows.xml",
    ".work/integration-v1.19.3.xml",
    ".work/integration-acl4ssr-v1.19.3-attempt-1.xml",
    ".work/integration-acl4ssr-v1.19.3-attempt-2.xml",
    ".work/integration-acl4ssr-v1.19.3-attempt-3.xml",
)
ARCHIVE_DISTRACTORS = (
    ".work/fixture-secrets.yaml",
    ".work/config.yaml",
    ".work/first.yaml",
    ".work/build.json",
    ".work/routing-v2-drift.json",
    ".work/bin/mihomo",
    ".work/wheel-venv/Lib/site-packages/clash_relay/unit-3.12.xml",
    ".work/acceptance-20261016/unit-3.12.xml",
    ".work/unit-3.12.xml.log",
)
FORBIDDEN_DECLARATIONS = (
    ".work",
    ".work/",
    ".work/**",
    ".work/**/*.xml",
    ".work/*",
    ".work/*.yaml",
    "config.yaml",
    "subscriptions.yaml",
    "policies.yaml",
    "rules",
    "policies",
    ".work/fixture-secrets.yaml",
    ".work/build.json",
    ".work/config.yaml",
)

# Observations from the pinned glob library, recorded 2026-10-16 on Windows with
# @actions/glob@0.6.1 and the options upload-artifact passes. A literal path
# below a hidden component still matches while hidden files are excluded, a
# literal path whose own basename is hidden does not, and every wildcard whose
# search descends through a hidden component returns nothing.
PINNED_GLOB_SEMANTICS = (
    (".work/unit-3.12.xml", ".work/unit-3.12.xml", False, True),
    (".work/unit-3.12.xml", ".work/unit-3.12.xml", True, True),
    (".work/*.xml", ".work/unit-3.12.xml", False, False),
    (".work/*.xml", ".work/unit-3.12.xml", True, True),
    (
        ".work/integration-acl4ssr-v1.19.3-attempt-*.xml",
        ".work/integration-acl4ssr-v1.19.3-attempt-2.xml",
        False,
        False,
    ),
    (
        ".work/integration-acl4ssr-v1.19.3-attempt-*.xml",
        ".work/integration-acl4ssr-v1.19.3-attempt-2.xml",
        True,
        True,
    ),
    (".work/**/*.xml", ".work/nested/deep.xml", False, False),
    (".work/**/*.xml", ".work/nested/deep.xml", True, True),
    (".w*/unit-3.12.xml", ".work/unit-3.12.xml", False, False),
    (".w*/unit-3.12.xml", ".work/unit-3.12.xml", True, True),
    ("**/*.xml", ".work/nested/deep.xml", False, False),
    ("**/*.xml", ".work/nested/deep.xml", True, True),
    ("**/*.xml", "reports/visible.xml", False, True),
    ("reports/*.xml", "reports/visible.xml", False, True),
    ("reports/*.xml", "reports/.hidden.xml", False, False),
    ("reports/*.xml", "reports/.hidden.xml", True, True),
    ("reports/.hidden.xml", "reports/.hidden.xml", False, False),
    ("reports/.hidden.xml", "reports/.hidden.xml", True, True),
)


class Declaration(NamedTuple):
    """One ``path`` line of an archive step, with the step's hidden-file setting."""

    pattern: str
    include_hidden: bool


def _compact(text: str) -> str:
    """Drop the spaces GitHub expressions carry, so a path can be compared as one token."""
    return EXPRESSION.sub(lambda match: "${{" + match.group(1) + "}}", text)


def _substitute(pattern: str, matrix: Mapping[str, str]) -> str:
    """Resolve the workflow's own matrix expressions into the value under test."""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        assert key in matrix, f"unexpected expression {key!r}"
        return str(matrix[key])

    return EXPRESSION.sub(replace, pattern)


def _ci(repo_root: Path) -> dict[str, Any]:
    text = (repo_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    document = yaml.safe_load(text)
    assert isinstance(document, dict)
    return document


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job.get("steps")
    assert isinstance(steps, list)
    return [step for step in steps if isinstance(step, dict)]


def _uploads(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        step
        for step in _steps(job)
        if isinstance(step.get("uses"), str) and step["uses"].startswith(f"{UPLOAD_ARTIFACT}@")
    ]


def _produced_reports(
    job: dict[str, Any],
    matrix: Mapping[str, str] = MATRIX,
    attempts: Sequence[str] = ATTEMPTS,
) -> set[str]:
    """Report paths the job's own commands write, resolved for the matrix and every attempt."""
    produced: set[str] = set()
    for step in _steps(job):
        run = step.get("run")
        if not isinstance(run, str):
            continue
        for match in PRODUCED_REPORT.finditer(_compact(run)):
            for attempt in attempts:
                candidate = match.group("path").replace("${attempt}", attempt).rstrip("\\;")
                produced.add(_substitute(candidate, matrix))
    return produced


def _declarations(job: dict[str, Any], matrix: Mapping[str, str] = MATRIX) -> list[Declaration]:
    declarations: list[Declaration] = []
    for step in _uploads(job):
        declared = step.get("with", {}).get("path")
        include_hidden = step.get("with", {}).get("include-hidden-files") is True
        if isinstance(declared, str):
            declarations.extend(
                Declaration(_substitute(_compact(line).strip(), matrix), include_hidden)
                for line in declared.splitlines()
                if line.strip()
            )
    return declarations


def _archive_paths(job: dict[str, Any]) -> set[str]:
    return {declaration.pattern for declaration in _declarations(job)}


def _has_magic(pattern: str) -> bool:
    return any(character in pattern for character in MAGIC)


def _match_segments(pattern: Sequence[str], path: Sequence[str]) -> bool:
    if not pattern:
        return not path
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return any(_match_segments(rest, path[index:]) for index in range(len(path) + 1))
    if not path:
        return False
    return fnmatch.fnmatchcase(path[0], head) and _match_segments(rest, path[1:])


def _glob_matches(declaration: Declaration, path: str) -> bool:
    """Mirror the pinned action's search for one declaration (see the module docstring)."""
    if not _match_segments(declaration.pattern.split("/"), path.split("/")):
        return False
    if declaration.include_hidden:
        return True
    if _has_magic(declaration.pattern):
        return not any(segment.startswith(".") for segment in path.split("/"))
    return not path.rsplit("/", 1)[-1].startswith(".")


def _matching(job: dict[str, Any], paths: Sequence[str]) -> set[str]:
    declarations = _declarations(job)
    return {path for path in paths if any(_glob_matches(d, path) for d in declarations)}


def _retention_violations(document: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    jobs = document.get("jobs")
    assert isinstance(jobs, dict)
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        uploads = _uploads(job)
        produced = _produced_reports(job)
        if not produced:
            continue
        if not uploads:
            violations.append(f"{job_name}: produces reports without an archive step")
            continue

        declarations = _declarations(job)
        for report in sorted(produced):
            if not any(_glob_matches(declaration, report) for declaration in declarations):
                violations.append(f"{job_name}: report {report} is not archived")

        for declaration in declarations:
            pattern = declaration.pattern
            if not pattern.startswith(".work/") or not pattern.endswith(REPORT_EXTENSION):
                violations.append(f"{job_name}: archive path {pattern} is not a whitelisted report")
            elif "*" in pattern.rsplit("/", 1)[0]:
                violations.append(f"{job_name}: archive path {pattern} globs a directory")
            elif not declaration.include_hidden:
                violations.append(
                    f"{job_name}: archive path {pattern} needs include-hidden-files "
                    "under a hidden directory"
                )

        names: dict[str, list[dict[str, Any]]] = {}
        for step in uploads:
            reference = str(step["uses"]).removeprefix(f"{UPLOAD_ARTIFACT}@")
            if not FULL_SHA.fullmatch(reference):
                violations.append(f"{job_name}: upload action {step['uses']} is not pinned")
            days = step.get("with", {}).get("retention-days")
            if not isinstance(days, int) or not 1 <= days <= SHORT_RETENTION_DAYS:
                violations.append(f"{job_name}: retention {days!r} is not short")
            names.setdefault(str(step.get("with", {}).get("name")), []).append(step)

        for name, group in sorted(names.items()):
            if not name or name == "None":
                violations.append(f"{job_name}: archive step without an artifact name")
                continue
            successes = [step for step in group if step.get("if") is None]
            failures = [step for step in group if step.get("if") == "failure()"]
            if not failures:
                violations.append(f"{job_name}: artifact {name} is not kept after a failed step")
            elif not successes:
                violations.append(f"{job_name}: artifact {name} is archived only after a failure")
            elif any(step.get("with") != successes[0].get("with") for step in failures):
                violations.append(
                    f"{job_name}: artifact {name} changes its declaration on the failure path"
                )
    return violations


def test_repository_ci_satisfies_the_report_retention_contract(repo_root: Path) -> None:
    assert _retention_violations(_ci(repo_root)) == []


def test_the_archive_matching_set_is_exactly_the_produced_reports(repo_root: Path) -> None:
    jobs = _ci(repo_root)["jobs"]
    listing = SYNTHETIC_REPORTS + ARCHIVE_DISTRACTORS

    matched: set[str] = set()
    for job in jobs.values():
        if isinstance(job, dict):
            matched.update(_matching(job, listing))

    assert matched == set(SYNTHETIC_REPORTS)


def test_every_archived_artifact_matches_its_failure_twin(repo_root: Path) -> None:
    jobs = _ci(repo_root)["jobs"]

    for job_name, job in jobs.items():
        for step in _uploads(job):
            if step.get("if") != "failure()":
                continue
            successes = [
                other
                for other in _uploads(job)
                if other.get("with", {}).get("name") == step.get("with", {}).get("name")
                and other.get("if") is None
            ]
            assert successes, f"{job_name}: failure archive has no success counterpart"
            assert successes[0].get("with") == step.get("with"), (
                f"{job_name}: failure archive changes the declaration"
            )


def test_retry_attempts_write_distinct_reports(repo_root: Path) -> None:
    job = _ci(repo_root)["jobs"]["stable-mihomo"]
    runs = "\n".join(
        str(step.get("run", "")) for step in _steps(job) if isinstance(step.get("run"), str)
    )

    assert "--junitxml=.work/integration-acl4ssr-" in runs
    assert "-attempt-${attempt}.xml" in runs
    assert "--junitxml=.work/integration-acl4ssr-${{ matrix.tag }}.xml" not in runs

    declarations = _declarations(job)
    for attempt in ATTEMPTS:
        report = f".work/integration-acl4ssr-{MATRIX['matrix.tag']}-attempt-{attempt}.xml"
        assert report in _produced_reports(job, attempts=(attempt,))
        assert any(_glob_matches(declaration, report) for declaration in declarations), (
            f"attempt {attempt} report is not archived"
        )


def test_secrets_and_generated_config_are_out_of_archive_scope(repo_root: Path) -> None:
    jobs = _ci(repo_root)["jobs"]
    declarations = [
        declaration
        for job in jobs.values()
        if isinstance(job, dict)
        for declaration in _declarations(job)
    ]

    assert declarations
    for forbidden in FORBIDDEN_DECLARATIONS + ARCHIVE_DISTRACTORS:
        assert not any(_glob_matches(declaration, forbidden) for declaration in declarations), (
            f"{forbidden} is inside the archive scope"
        )


def test_a_literal_hidden_path_matches_without_the_opt_in_but_still_violates_the_contract(
    repo_root: Path,
) -> None:
    """A literal path survives hidden exclusion, so only the explicit rule catches it."""
    document = copy.deepcopy(_ci(repo_root))
    for step in _uploads(document["jobs"]["quality"]):
        step["with"].pop("include-hidden-files", None)

    quality_violations = [
        violation
        for violation in _retention_violations(document)
        if violation.startswith("quality:")
    ]

    assert not any("is not archived" in violation for violation in quality_violations), (
        quality_violations
    )
    assert any("needs include-hidden-files" in violation for violation in quality_violations), (
        quality_violations
    )


@pytest.mark.parametrize(
    ("pattern", "path", "include_hidden", "expected"),
    PINNED_GLOB_SEMANTICS,
)
def test_glob_model_matches_the_pinned_action_semantics(
    pattern: str, path: str, include_hidden: bool, expected: bool
) -> None:
    assert _glob_matches(Declaration(pattern, include_hidden), path) is expected


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("whole_work_directory", ("is not a whitelisted report",)),
        ("dropped_archive_step", ("without an archive step",)),
        ("long_retention", ("is not short",)),
        ("floating_reference", ("is not pinned",)),
        ("success_only", ("is not kept after a failed step",)),
        ("shared_retry_path", ("is not archived",)),
        ("hidden_optin_removed", ("is not archived", "needs include-hidden-files")),
        ("hidden_optin_widened_to_every_file", ("is not a whitelisted report",)),
        ("failure_path_without_opt_in", ("changes its declaration on the failure path",)),
        ("narrowed_retry_glob", ("is not archived",)),
    ],
)
def test_single_property_drift_is_rejected(
    repo_root: Path, mutation: str, expected: tuple[str, ...]
) -> None:
    document = copy.deepcopy(_ci(repo_root))
    jobs = document["jobs"]
    quality = jobs["quality"]
    uploads = _uploads(quality)

    if mutation == "whole_work_directory":
        uploads[0]["with"]["path"] = ".work\n"
    elif mutation == "dropped_archive_step":
        quality["steps"] = [step for step in quality["steps"] if step not in uploads]
    elif mutation == "long_retention":
        for step in uploads:
            step["with"]["retention-days"] = 90
    elif mutation == "floating_reference":
        for step in uploads:
            step["uses"] = f"{UPLOAD_ARTIFACT}@v7"
    elif mutation == "success_only":
        quality["steps"] = [step for step in quality["steps"] if step.get("if") != "failure()"]
    elif mutation == "shared_retry_path":
        for step in _steps(jobs["stable-mihomo"]):
            run = step.get("run")
            if isinstance(run, str):
                step["run"] = run.replace("-attempt-${attempt}.xml", ".xml")
    elif mutation == "hidden_optin_removed":
        for step in _uploads(jobs["stable-mihomo"]):
            step["with"].pop("include-hidden-files", None)
    elif mutation == "hidden_optin_widened_to_every_file":
        for step in _uploads(jobs["stable-mihomo"]):
            step["with"]["path"] = ".work/**\n"
    elif mutation == "failure_path_without_opt_in":
        for step in _uploads(jobs["stable-mihomo"]):
            if step.get("if") == "failure()":
                step["with"].pop("include-hidden-files", None)
    elif mutation == "narrowed_retry_glob":
        for step in _uploads(jobs["stable-mihomo"]):
            step["with"]["path"] = step["with"]["path"].replace("-attempt-*.xml", "-attempt-1.xml")
    else:  # pragma: no cover - the parametrization is exhaustive
        raise AssertionError(mutation)

    violations = _retention_violations(document)

    for fragment in expected:
        assert any(fragment in violation for violation in violations), (fragment, violations)
