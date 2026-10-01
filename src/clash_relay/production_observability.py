"""Consolidated best-effort production observability.

This module owns optional post-release metrics, scheduler evidence, and
operational SLO persistence. None of these operations may change release
validity or widen routing/source permissions.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config_loader import ProjectDefinition
from .errors import ClashRelayError, PublicationError, ValidationError
from .operational_slo import (
    ProductionOutcome,
    append_slo_attempt,
    build_slo_attempt,
    parse_slo_bytes,
    qualification_failure_category,
    qualification_retry_attempted,
    slo_summary,
)
from .production_application import persist_production_metrics
from .production_metrics import parse_metrics_bytes
from .publishers.cloudflare_kv import CloudflareKVPublisher
from .scheduler_evidence import compile_scheduler_evidence
from .util import atomic_write


@dataclass(frozen=True, slots=True)
class PostReleaseObservability:
    production_metrics: dict[str, Any]
    scheduler_observation: dict[str, Any]
    operational_slo: dict[str, Any]
    warnings: tuple[str, ...]
    timings_ms: dict[str, float]


@dataclass(frozen=True, slots=True)
class FailureObservability:
    operational_slo: dict[str, Any]
    warnings: tuple[str, ...]


def _environment(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _write_json(path: Path, document: dict[str, Any]) -> None:
    atomic_write(
        path,
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"production observability could not read {path.name!r}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"production observability JSON {path.name!r} must be an object")
    return value


def _best_effort(
    stage: str,
    operation: Callable[[], dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    try:
        return operation()
    except (OSError, ValueError, ClashRelayError):
        warnings.append(stage)
        return {"status": "unavailable", "reason": "stage_failed"}


def _warn_unavailable(stage: str, result: dict[str, Any], warnings: list[str]) -> None:
    if result.get("status") == "unavailable" and stage not in warnings:
        warnings.append(stage)


def _production_key(project: ProjectDefinition) -> str:
    return str(project.config["publishing"]["cloudflare_kv"]["key"])


def _cloudflare_values(env: Mapping[str, str]) -> tuple[str, str, str]:
    return (
        env.get("CLOUDFLARE_API_TOKEN", "").strip(),
        env.get("CLOUDFLARE_ACCOUNT_ID", "").strip(),
        env.get("CLOUDFLARE_KV_NAMESPACE_TITLE", "").strip(),
    )


def _publish_scheduler_observation(
    *,
    project: ProjectDefinition,
    env: Mapping[str, str],
) -> dict[str, Any]:
    token, account_id, namespace_title = _cloudflare_values(env)
    if not token or not account_id or not namespace_title:
        return {"status": "skipped", "reason": "cloudflare_unavailable"}

    production_key = _production_key(project)
    try:
        metrics = CloudflareKVPublisher(
            token=token,
            account_id=account_id,
            namespace_title=namespace_title,
            key_name=f"{production_key}.production-metrics-v1",
        ).read()
    except PublicationError:
        return {"status": "unavailable", "reason": "metrics_read_failed"}

    state, load_status = parse_metrics_bytes(metrics)
    if load_status != "loaded":
        return {"status": "skipped", "reason": f"metrics_{load_status}"}

    evidence = compile_scheduler_evidence(state)
    content = (
        json.dumps(
            evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    try:
        published = CloudflareKVPublisher(
            token=token,
            account_id=account_id,
            namespace_title=namespace_title,
            key_name=f"{production_key}.scheduler-evidence-v1",
        ).publish(content=content)
    except PublicationError:
        return {"status": "unavailable", "reason": "evidence_publish_failed"}

    return {
        "status": "published",
        "load_status": load_status,
        "mode": evidence["mode"],
        "privacy": evidence["privacy"],
        "evidence_status": evidence["status"],
        "sample_runs": evidence["sample_runs"],
        "bytes": published["bytes"],
        "sha256": published["sha256"],
    }


def _persist_operational_slo(
    *,
    project: ProjectDefinition,
    attempt: dict[str, Any],
    env: Mapping[str, str],
) -> dict[str, Any]:
    token, account_id, namespace_title = _cloudflare_values(env)
    if not token or not account_id or not namespace_title:
        return {"status": "skipped", "reason": "cloudflare_unavailable"}

    production_key = _production_key(project)
    publisher = CloudflareKVPublisher(
        token=token,
        account_id=account_id,
        namespace_title=namespace_title,
        key_name=f"{production_key}.operational-slo-v1",
    )
    try:
        existing = publisher.read()
        state, load_status = parse_slo_bytes(existing)
        next_state = append_slo_attempt(state, attempt)
        content = (
            json.dumps(
                next_state,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        published = publisher.publish(content=content)
    except (ValueError, PublicationError):
        return {"status": "unavailable"}
    return {
        "status": "published",
        "load_status": load_status,
        "bytes": published["bytes"],
        **slo_summary(next_state),
    }


def _candidate_identity(private_dir: Path) -> tuple[str | None, int | None]:
    for path in (private_dir / "config.yaml", private_dir / "generated.yaml"):
        try:
            content = path.read_bytes()
        except OSError:
            continue
        if content:
            return hashlib.sha256(content).hexdigest(), len(content)
    return None, None


def _qualification_retry_state(private_dir: Path) -> tuple[bool, bool]:
    path = private_dir / "qualification-pipeline-summary.json"
    if not path.is_file():
        return False, False
    try:
        summary = _load_json(path)
    except ValidationError:
        return False, False
    browsing = summary.get("browsing")
    if not isinstance(browsing, dict):
        return False, False
    attempts = browsing.get("stage_attempts", 1)
    retry_attempted = isinstance(attempts, int) and not isinstance(attempts, bool) and attempts > 1
    return retry_attempted, browsing.get("recovered_by_retry") is True


def _promotion_state(private_dir: Path) -> tuple[bool, bool]:
    path = private_dir / "promotion-guard.json"
    if not path.is_file():
        return False, False
    try:
        report = _load_json(path)
    except ValidationError:
        return False, False
    status = report.get("status")
    return status in {"passed", "blocked"}, status == "blocked"


def _record_operational_slo(
    *,
    project: ProjectDefinition,
    publish: bool,
    private_dir: Path,
    lifecycle_started: float,
    outcome: ProductionOutcome,
    env: Mapping[str, str],
    warnings: list[str],
    failure_category: str | None = None,
    failure_retry_attempted: bool = False,
) -> dict[str, Any]:
    if not publish:
        return {"status": "skipped", "reason": "dry_run"}

    candidate_sha256, candidate_bytes = _candidate_identity(private_dir)
    retry_attempted, retry_recovered = _qualification_retry_state(private_dir)
    retry_attempted = retry_attempted or failure_retry_attempted
    guard_checked, guard_blocked = _promotion_state(private_dir)
    try:
        attempt = build_slo_attempt(
            outcome=outcome,
            duration_ms=(time.perf_counter() - lifecycle_started) * 1000.0,
            candidate_sha256=candidate_sha256,
            candidate_bytes=candidate_bytes,
            qualification_failure_category=failure_category,
            retry_attempted=retry_attempted,
            retry_recovered=retry_recovered,
            promotion_guard_checked=guard_checked,
            promotion_guard_blocked=guard_blocked,
        )
        result = _persist_operational_slo(project=project, attempt=attempt, env=env)
        _write_json(private_dir / "operational-slo-publish.json", result)
        return result
    except (OSError, ValueError, ClashRelayError):
        warnings.append("persist_operational_slo")
        return {"status": "unavailable", "reason": "stage_failed"}


def publish_post_release_observability(
    *,
    project: ProjectDefinition,
    publish: bool,
    private_dir: Path,
    lifecycle_started: float,
    env: Mapping[str, str] | None = None,
) -> PostReleaseObservability:
    """Publish optional aggregate observability after the release boundary."""

    values = _environment(env)
    warnings: list[str] = []
    timings_ms: dict[str, float] = {}

    if not publish:
        skipped = {"status": "skipped", "reason": "dry_run"}
        return PostReleaseObservability(
            production_metrics=dict(skipped),
            scheduler_observation=dict(skipped),
            operational_slo=dict(skipped),
            warnings=(),
            timings_ms={},
        )

    started = time.perf_counter()
    metrics = _best_effort(
        "persist_production_metrics",
        lambda: persist_production_metrics(
            project=project,
            private_dir=private_dir,
            env=values,
        ),
        warnings,
    )
    timings_ms["production_metrics"] = round(
        (time.perf_counter() - started) * 1000.0,
        3,
    )
    _warn_unavailable("persist_production_metrics", metrics, warnings)
    _write_json(private_dir / "production-metrics-publish.json", metrics)

    if metrics.get("status") == "published":
        started = time.perf_counter()
        scheduler = _best_effort(
            "publish_scheduler_observation",
            lambda: _publish_scheduler_observation(project=project, env=values),
            warnings,
        )
        timings_ms["scheduler_observation"] = round(
            (time.perf_counter() - started) * 1000.0,
            3,
        )
        _warn_unavailable("publish_scheduler_observation", scheduler, warnings)
        _write_json(private_dir / "scheduler-observation-publish.json", scheduler)
    else:
        scheduler = {
            "status": "skipped",
            "reason": "production_metrics_not_published",
        }

    slo = _record_operational_slo(
        project=project,
        publish=True,
        private_dir=private_dir,
        lifecycle_started=lifecycle_started,
        outcome=ProductionOutcome.PASSED,
        env=values,
        warnings=warnings,
    )
    return PostReleaseObservability(
        production_metrics=metrics,
        scheduler_observation=scheduler,
        operational_slo=slo,
        warnings=tuple(warnings),
        timings_ms=timings_ms,
    )


def record_failure_observability(
    *,
    project: ProjectDefinition,
    publish: bool,
    private_dir: Path,
    lifecycle_started: float,
    error: Exception,
    env: Mapping[str, str] | None = None,
) -> FailureObservability:
    """Record one best-effort failed-attempt SLO without changing the failure."""

    values = _environment(env)
    warnings: list[str] = []
    category = qualification_failure_category(error)
    guard_checked, guard_blocked = _promotion_state(private_dir)
    outcome = (
        ProductionOutcome.QUALIFICATION_REJECTED
        if category is not None
        else ProductionOutcome.PROMOTION_BLOCKED
        if guard_checked and guard_blocked
        else ProductionOutcome.FAILED
    )
    slo = _record_operational_slo(
        project=project,
        publish=publish,
        private_dir=private_dir,
        lifecycle_started=lifecycle_started,
        outcome=outcome,
        env=values,
        warnings=warnings,
        failure_category=category,
        failure_retry_attempted=qualification_retry_attempted(error),
    )
    return FailureObservability(
        operational_slo=slo,
        warnings=tuple(warnings),
    )
