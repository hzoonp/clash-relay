"""Application boundary for production candidate qualification and auditing.

PolicyContract owns declaration truth, RuntimeGraph owns topology truth, and
this module owns execution order for the mutable production candidate stages.
Publication transactions intentionally remain in ``release_bundle``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .acl4ssr_reference import validate_acl4ssr_fidelity
from .ai_runtime_reliability import audit_openai_client_path
from .config_loader import ProjectDefinition, load_project
from .errors import ValidationError
from .mihomo import load_candidate
from .openai_app_contract import audit_route_lock
from .production_audit import audit_production_candidate, render_production_summary_markdown
from .qualification_pipeline import run_qualification_pipeline
from .routing_v2_audit import audit_routing_v2
from .util import atomic_write


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    config: Path
    subscriptions: Path
    services: Path
    policies: Path

    def load(self) -> ProjectDefinition:
        return load_project(
            config_path=self.config,
            subscriptions_path=self.subscriptions,
            services_path=self.services,
            policies_path=self.policies,
        )


@dataclass(frozen=True, slots=True)
class QualificationPaths:
    candidate: Path
    output: Path
    mihomo_bin: Path
    stage_dir: Path
    browsing_report: Path
    ai_report: Path
    history: Path | None = None
    history_key: Path | None = None
    next_history: Path | None = None
    cache: Path | None = None
    cache_key: Path | None = None
    next_cache: Path | None = None


@dataclass(frozen=True, slots=True)
class ProductionPipelineOutputs:
    pre_audit: Path
    post_audit: Path
    qualification: Path
    summary_markdown: Path | None = None


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(
            f"production pipeline could not read JSON input {path.name!r}"
        ) from exc
    if not isinstance(value, dict):
        raise ValidationError(f"production pipeline JSON input {path.name!r} must be an object")
    return value


def _write_json(path: Path, document: dict[str, Any]) -> None:
    atomic_write(
        path,
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def audit_candidate(
    project: ProjectDefinition,
    candidate: dict[str, Any],
    *,
    build_report: dict[str, Any] | None = None,
    allow_legacy_openai_client_path: bool = False,
) -> dict[str, Any]:
    """Run the complete production audit contract over one candidate stage."""

    summary = audit_production_candidate(project, candidate, build_report=build_report)
    summary["routing_v2"] = audit_routing_v2(project, candidate)
    summary["openai_app"] = audit_route_lock(candidate)
    summary["openai_client_path"] = audit_openai_client_path(
        candidate,
        allow_legacy_server_qualified=allow_legacy_openai_client_path,
    )
    if project.acl4ssr is not None and project.acl4ssr.get("reference") is not None:
        reference_path = project.root / "rules/acl4ssr-online.reference.ini"
        try:
            reference_text = reference_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValidationError("production pipeline could not read ACL4SSR reference") from exc
        summary["acl4ssr_fidelity"] = validate_acl4ssr_fidelity(
            project.acl4ssr,
            reference_text=reference_text,
        )
    return summary


def render_qualification_summary_markdown(
    browsing: dict[str, Any],
    ai: dict[str, Any],
) -> str:
    """Render aggregate-only qualification details for GitHub Actions."""

    browsing_diagnostics = browsing.get("diagnostics", {})
    if not isinstance(browsing_diagnostics, dict):
        browsing_diagnostics = {}
    latency = browsing_diagnostics.get("qualified_latency_ms", {})
    if not isinstance(latency, dict):
        latency = {}
    history = browsing.get("scheduler_history", {})
    if not isinstance(history, dict):
        history = {}

    ai_diagnostics = ai.get("diagnostics", {})
    if not isinstance(ai_diagnostics, dict):
        ai_diagnostics = {}
    probes = ai_diagnostics.get("probes", {})
    if not isinstance(probes, dict):
        probes = {}
    cache = ai.get("qualification_cache", {})
    if not isinstance(cache, dict):
        cache = {}

    lines = [
        "## Browsing qualification",
        "",
        f"Tested nodes: **{int(browsing_diagnostics.get('tested_nodes', 0) or 0)}**  ",
        f"Qualified nodes: **{int(browsing_diagnostics.get('qualified_nodes', 0) or 0)}**  ",
        f"Stable / reserve: **{int(browsing.get('stable_nodes', 0) or 0)} / {int(browsing.get('reserve_nodes', 0) or 0)}**  ",
        f"Automatic nodes: **{int(browsing.get('automatic_nodes', 0) or 0)}**  ",
        f"Historically demoted stable nodes: **{int(history.get('historically_demoted_nodes', 0) or 0)}**  ",
        f"Scheduler history: **{history.get('status', 'disabled')}** ({int(history.get('records_before', 0) or 0)} → {int(history.get('records_after', 0) or 0)} anonymous records)  ",
        f"Rejected nodes: **{int(browsing_diagnostics.get('failed_nodes', 0) or 0)}**  ",
        f"Qualification threshold: **{int(browsing_diagnostics.get('required_successes', 0) or 0)}/{int(browsing_diagnostics.get('attempts_per_node', 0) or 0)} successful HTTPS probes**",
        "",
        "| Qualified latency | ms |",
        "| --- | ---: |",
        f"| p50 | {latency.get('p50', 'n/a')} |",
        f"| p95 | {latency.get('p95', 'n/a')} |",
        "",
        "Only aggregate browsing qualification and anonymous-history counts are shown; node-level results remain private.",
        "",
        "## AI qualification",
        "",
        f"Candidate nodes: **{int(ai_diagnostics.get('tested_nodes', 0) or 0)}**  ",
        f"Live service probes: **{int(cache.get('live_service_probes', 0) or 0)}**  ",
        f"Fresh cache pass/fail hits: **{int(cache.get('cache_pass_hits', 0) or 0)} / {int(cache.get('cache_fail_hits', 0) or 0)}**  ",
        f"Selector failures: **{int(ai_diagnostics.get('selector_failures', 0) or 0)}**",
        "",
        "| Service probe | Live tested | Qualified nodes |",
        "| --- | ---: | ---: |",
    ]
    for name in sorted(probes):
        probe = probes[name]
        if not isinstance(probe, dict):
            continue
        lines.append(
            f"| `{name}` | {int(probe.get('live_tested_nodes', 0) or 0)} | {int(probe.get('qualified_nodes', 0) or 0)} |"
        )
    lines.extend(
        [
            "",
            "Only aggregate qualification/cache counts are shown; node-level results remain private.",
            "",
        ]
    )
    return "\n".join(lines)


def run_production_pipeline(
    *,
    project_paths: ProjectPaths,
    qualification_paths: QualificationPaths,
    outputs: ProductionPipelineOutputs,
    build_report_path: Path | None = None,
    workers: int = 12,
    script_dir: Path | None = None,
    python_executable: str | None = None,
) -> dict[str, Any]:
    """Run pre-audit, qualification, post-audit, and safe summary rendering."""

    project = project_paths.load()
    build_report = _load_json(build_report_path) if build_report_path is not None else None

    generated = load_candidate(qualification_paths.candidate)
    pre_audit = audit_candidate(project, generated, build_report=build_report)
    _write_json(outputs.pre_audit, pre_audit)

    qualification = run_qualification_pipeline(
        candidate=qualification_paths.candidate,
        output=qualification_paths.output,
        policies=project_paths.policies,
        mihomo_bin=qualification_paths.mihomo_bin,
        stage_dir=qualification_paths.stage_dir,
        browsing_report=qualification_paths.browsing_report,
        ai_report=qualification_paths.ai_report,
        workers=workers,
        history=qualification_paths.history,
        history_key=qualification_paths.history_key,
        next_history=qualification_paths.next_history,
        cache=qualification_paths.cache,
        cache_key=qualification_paths.cache_key,
        next_cache=qualification_paths.next_cache,
        script_dir=script_dir,
        python_executable=python_executable,
    )
    _write_json(outputs.qualification, qualification)

    qualified = load_candidate(qualification_paths.output)
    post_audit = audit_candidate(project, qualified, build_report=build_report)
    _write_json(outputs.post_audit, post_audit)

    if outputs.summary_markdown is not None:
        browsing = _load_json(qualification_paths.browsing_report)
        ai = _load_json(qualification_paths.ai_report)
        markdown = render_production_summary_markdown(pre_audit)
        markdown += "\n" + render_qualification_summary_markdown(browsing, ai)
        atomic_write(outputs.summary_markdown, markdown)

    # Deliberately aggregate-only. The detailed stage reports remain private files.
    return {
        **qualification,
        "production_pipeline": {
            "status": "passed",
            "pre_audit": pre_audit.get("status"),
            "post_audit": post_audit.get("status"),
            "routing_v2": post_audit.get("routing_v2", {}).get("status")
            if isinstance(post_audit.get("routing_v2"), dict)
            else "unknown",
        },
    }
