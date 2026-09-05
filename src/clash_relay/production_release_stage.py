"""Typed release-candidate stage for promotion, real-core validation, and activation."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config_loader import ProjectDefinition
from .mihomo_matrix_application import validate_mihomo_matrix
from .production_application import (
    fetch_current_production_config,
    publish_production_release,
    run_promotion_guard,
)
from .util import atomic_write


@dataclass(frozen=True, slots=True)
class ReleaseCandidateStagePaths:
    candidate: Path
    qualification: Path
    baseline: Path
    baseline_report: Path
    guard_policy: Path
    guard_report: Path
    guard_markdown: Path
    mihomo_manifest: Path
    mihomo_work_dir: Path
    matrix_report: Path
    release_report: Path


@dataclass(frozen=True, slots=True)
class ReleaseCandidateStageResult:
    promotion: dict[str, Any]
    matrix: dict[str, Any]
    release: dict[str, Any] | None
    timings_ms: dict[str, float]


def _write_json(path: Path, document: dict[str, Any]) -> None:
    atomic_write(
        path,
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)


def run_release_candidate_stage(
    *,
    project: ProjectDefinition,
    publish: bool,
    primary_binary: Path,
    paths: ReleaseCandidateStagePaths,
    env: Mapping[str, str] | None = None,
) -> ReleaseCandidateStageResult:
    """Promote one qualified candidate without owning lifecycle orchestration.

    The ordering is deliberately fail closed: production baseline + Promotion
    Guard first, then the complete stable Mihomo matrix, then private release
    activation. A failure in either gate prevents publication.
    """

    timings: dict[str, float] = {}

    started = time.perf_counter()
    if publish:
        baseline = fetch_current_production_config(
            project=project,
            output=paths.baseline,
            allow_missing=True,
            env=env,
        )
        _write_json(paths.baseline_report, baseline)
        promotion = run_promotion_guard(
            project=project,
            candidate_path=paths.candidate,
            baseline_path=paths.baseline,
            guard_path=paths.guard_policy,
            qualification_path=paths.qualification,
            report_path=paths.guard_report,
            markdown_path=paths.guard_markdown,
        )
    else:
        promotion = {"status": "skipped", "reason": "dry_run"}
    timings["promotion_guard"] = _elapsed_ms(started)

    started = time.perf_counter()
    matrix = validate_mihomo_matrix(
        candidate=paths.candidate,
        manifest=paths.mihomo_manifest,
        channel="stable",
        work_dir=paths.mihomo_work_dir,
        reuse_primary_bin=primary_binary,
    )
    _write_json(paths.matrix_report, matrix)
    timings["mihomo_matrix"] = _elapsed_ms(started)

    started = time.perf_counter()
    release: dict[str, Any] | None = None
    if publish:
        release = publish_production_release(
            project=project,
            candidate_path=paths.candidate,
            env=env,
        )
        _write_json(paths.release_report, release)
    timings["publication"] = _elapsed_ms(started)

    return ReleaseCandidateStageResult(
        promotion=promotion,
        matrix=matrix,
        release=release,
        timings_ms=timings,
    )
