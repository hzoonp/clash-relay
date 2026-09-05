"""In-process application services used by the production lifecycle and CLI adapters."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .ai_qualification_cache import ai_cache_summary, derive_ai_cache_key, parse_ai_cache_bytes
from .config_loader import ProjectDefinition
from .errors import PublicationError, ValidationError
from .mihomo import load_candidate
from .production_metrics import (
    append_metrics_run,
    build_metrics_run,
    metrics_summary,
    parse_metrics_bytes,
)
from .production_proof import build_production_proof, render_production_proof_markdown
from .promotion_guard import assess_promotion, load_promotion_guard_policy
from .publication import publication_gate
from .publishers.cloudflare_kv import CloudflareKVPublisher
from .release_bundle import publish_release_bundle as commit_release_bundle
from .scheduler_history import derive_fingerprint_key, parse_history_bytes
from .util import atomic_write
from .validator import validate_generated_config


def _environment(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _credentials(env: Mapping[str, str] | None) -> tuple[str, str, str]:
    values = _environment(env)
    return (
        values.get("CLOUDFLARE_API_TOKEN", ""),
        values.get("CLOUDFLARE_ACCOUNT_ID", ""),
        values.get("CLOUDFLARE_KV_NAMESPACE_TITLE", ""),
    )


def _production_key(project: ProjectDefinition) -> str:
    return str(project.config["publishing"]["cloudflare_kv"]["key"])


def _publisher(
    *, token: str, account_id: str, namespace_title: str, key_name: str
) -> CloudflareKVPublisher:
    return CloudflareKVPublisher(
        token=token,
        account_id=account_id,
        namespace_title=namespace_title,
        key_name=key_name,
    )


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"failed to load {label}") from exc
    if not isinstance(document, dict):
        raise ValidationError(f"{label} must be a JSON mapping")
    return document


def _optional_json(path: Path) -> dict[str, Any] | None:
    return _load_json(path, path.name) if path.is_file() else None


def load_scheduler_history_state(
    *,
    project: ProjectDefinition,
    output: Path,
    fingerprint_key_output: Path,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Load the newest available private browsing scheduler state from KV."""

    token, account_id, namespace_title = _credentials(env)
    production_key = _production_key(project)
    content: bytes | None = None
    transport_status = "unavailable"
    source = "none"
    if token and account_id and namespace_title:
        for suffix, source_name in (
            ("scheduler-state-v3", "v3"),
            ("scheduler-state-v2", "v2"),
            ("scheduler-state-v1", "v1"),
        ):
            try:
                content = _publisher(
                    token=token,
                    account_id=account_id,
                    namespace_title=namespace_title,
                    key_name=f"{production_key}.{suffix}",
                ).read()
            except PublicationError:
                content = None
                transport_status = "unavailable"
                source = "none"
                break
            if content is not None:
                transport_status = "loaded"
                source = source_name
                break
        else:
            transport_status = "missing"

    history, parse_status = parse_history_bytes(content)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(history, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    fingerprint_key_output.parent.mkdir(parents=True, exist_ok=True)
    if token and transport_status in {"loaded", "missing"}:
        fingerprint_key_output.write_text(
            derive_fingerprint_key(token).hex() + "\n", encoding="ascii"
        )
    else:
        fingerprint_key_output.write_text("", encoding="ascii")
    os.chmod(fingerprint_key_output, 0o600)

    nodes = history.get("nodes", {})
    return {
        "status": transport_status,
        "source": source,
        "parse_status": parse_status,
        "state_version": int(history.get("version", 0)),
        "records": len(nodes) if isinstance(nodes, dict) else 0,
    }


def load_ai_qualification_cache_state(
    *,
    project: ProjectDefinition,
    output: Path,
    fingerprint_key_output: Path,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Load the private AI qualification cache and derive its local fingerprint key."""

    token, account_id, namespace_title = _credentials(env)
    cache_key = f"{_production_key(project)}.ai-qualification-cache-v1"
    content: bytes | None = None
    transport_status = "unavailable"
    if token and account_id and namespace_title:
        try:
            content = _publisher(
                token=token,
                account_id=account_id,
                namespace_title=namespace_title,
                key_name=cache_key,
            ).read()
            transport_status = "loaded" if content is not None else "missing"
        except PublicationError:
            transport_status = "unavailable"

    cache, parse_status = parse_ai_cache_bytes(content)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(cache, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    fingerprint_key_output.parent.mkdir(parents=True, exist_ok=True)
    if token and transport_status in {"loaded", "missing"}:
        fingerprint_key_output.write_text(derive_ai_cache_key(token).hex() + "\n", encoding="ascii")
    else:
        fingerprint_key_output.write_text("", encoding="ascii")
    os.chmod(fingerprint_key_output, 0o600)

    nodes = cache.get("nodes", {})
    return {
        "status": transport_status,
        "parse_status": parse_status,
        "records": len(nodes) if isinstance(nodes, dict) else 0,
    }


def fetch_current_production_config(
    *,
    project: ProjectDefinition,
    output: Path,
    allow_missing: bool = False,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Fetch the exact client-visible production value used as the promotion baseline."""

    token, account_id, namespace_title = _credentials(env)
    if not token or not account_id or not namespace_title:
        raise PublicationError("Cloudflare credentials are required for production baseline")
    current = _publisher(
        token=token,
        account_id=account_id,
        namespace_title=namespace_title,
        key_name=_production_key(project),
    ).read()
    if current is None:
        if not allow_missing:
            raise PublicationError("current production release is missing")
        output.unlink(missing_ok=True)
        return {"status": "absent"}
    if not current:
        raise PublicationError("current production release is empty")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(current)
    os.chmod(output, 0o600)
    return {
        "status": "fetched",
        "bytes": len(current),
        "sha256": hashlib.sha256(current).hexdigest(),
    }


def render_promotion_guard_markdown(report: dict[str, Any]) -> str:
    candidate = report.get("candidate", {})
    baseline = report.get("baseline", {})
    ratios = report.get("ratios", {})
    violations = report.get("violations", [])
    lines = [
        "## Production promotion guard",
        "",
        f"Decision: **{report.get('status', 'unknown')}**  ",
        f"Reason: **{report.get('reason', 'unknown')}**",
        "",
    ]
    if isinstance(candidate, dict):
        lines.append(
            f"Candidate inventory: **{int(candidate.get('nodes', 0))} nodes / {int(candidate.get('providers', 0))} providers**  "
        )
    if isinstance(baseline, dict):
        lines.append(
            f"Production baseline: **{int(baseline.get('nodes', 0))} nodes / {int(baseline.get('providers', 0))} providers**  "
        )
    if isinstance(ratios, dict) and ratios:
        lines.append(
            f"Candidate/baseline ratios: **nodes {ratios.get('total_nodes', 'n/a')} / providers {ratios.get('providers', 'n/a')}**"
        )
    if violations:
        lines.extend(
            ["", "Blocked checks: **" + ", ".join(str(item) for item in violations) + "**"]
        )
    lines.extend(
        [
            "",
            "Only aggregate inventory counts are emitted; node names, servers, and credentials remain private.",
            "",
        ]
    )
    return "\n".join(lines)


def run_promotion_guard(
    *,
    project: ProjectDefinition,
    candidate_path: Path,
    baseline_path: Path,
    guard_path: Path,
    qualification_path: Path | None = None,
    report_path: Path | None = None,
    markdown_path: Path | None = None,
) -> dict[str, Any]:
    """Assess promotion using typed candidate and aggregate qualification inputs."""

    candidate = load_candidate(candidate_path)
    baseline = load_candidate(baseline_path) if baseline_path.is_file() else None
    policy = load_promotion_guard_policy(guard_path)
    qualification_source = qualification_path or candidate_path.with_name(
        "qualification-pipeline-summary.json"
    )
    qualification = _optional_json(qualification_source)
    report = assess_promotion(
        project,
        candidate,
        baseline,
        policy,
        qualification=qualification,
    )
    if report_path is not None:
        atomic_write(
            report_path,
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
    if markdown_path is not None:
        atomic_write(markdown_path, render_promotion_guard_markdown(report))
    if report.get("status") == "blocked":
        raise ValidationError("promotion guard blocked the candidate")
    if report.get("status") != "passed":
        raise ValidationError("promotion guard returned an invalid decision")
    return report


def publish_production_release(
    *,
    project: ProjectDefinition,
    candidate_path: Path,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate, stage, verify, and activate one private Cloudflare KV release."""

    publication_gate(project.config, "cloudflare_kv")
    candidate = load_candidate(candidate_path)
    validate_generated_config(candidate)
    try:
        content = candidate_path.read_bytes()
    except OSError as exc:
        raise PublicationError("failed to read production release candidate") from exc
    if not content:
        raise PublicationError("refusing to publish an empty production release candidate")
    token, account_id, namespace_title = _credentials(env)
    if not token or not account_id or not namespace_title:
        raise PublicationError("Cloudflare credentials are required for production publication")
    production_key = _production_key(project)

    def factory(key: str) -> CloudflareKVPublisher:
        return _publisher(
            token=token,
            account_id=account_id,
            namespace_title=namespace_title,
            key_name=key,
        )

    return commit_release_bundle(factory=factory, production_key=production_key, content=content)


def persist_ai_qualification_cache(
    *,
    project: ProjectDefinition,
    state: Path,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    try:
        content = state.read_bytes()
    except OSError:
        return {"status": "skipped", "reason": "state_missing"}
    cache, parse_status = parse_ai_cache_bytes(content)
    if parse_status == "invalid":
        return {"status": "skipped", "reason": "state_invalid"}
    content = (
        json.dumps(cache, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    token, account_id, namespace_title = _credentials(env)
    if not token or not account_id or not namespace_title:
        return {"status": "skipped", "reason": "cloudflare_unavailable"}
    try:
        result = _publisher(
            token=token,
            account_id=account_id,
            namespace_title=namespace_title,
            key_name=f"{_production_key(project)}.ai-qualification-cache-v1",
        ).publish(content=content)
    except PublicationError:
        return {"status": "unavailable", "records_preserved": False}
    return {
        "status": "published",
        "bytes": result["bytes"],
        "sha256": result["sha256"],
        **ai_cache_summary(cache),
    }


def persist_scheduler_history(
    *,
    project: ProjectDefinition,
    state: Path,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    token, account_id, namespace_title = _credentials(env)
    if not token or not account_id or not namespace_title:
        return {"status": "skipped", "reason": "cloudflare_unavailable"}
    try:
        content = state.read_bytes()
    except OSError:
        return {"status": "skipped", "reason": "state_missing"}
    history, parse_status = parse_history_bytes(content)
    if parse_status == "invalid":
        return {"status": "skipped", "reason": "state_invalid"}
    content = (
        json.dumps(history, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    try:
        result = _publisher(
            token=token,
            account_id=account_id,
            namespace_title=namespace_title,
            key_name=f"{_production_key(project)}.scheduler-state-v3",
        ).publish(content=content)
    except PublicationError:
        return {"status": "unavailable", "records_preserved": False}
    return {
        "status": "published",
        "state_version": int(history.get("version", 0)),
        "bytes": result["bytes"],
        "sha256": result["sha256"],
        "records": len(history["nodes"]),
    }


def persist_production_metrics(
    *,
    project: ProjectDefinition,
    private_dir: Path,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    token, account_id, namespace_title = _credentials(env)
    if not token or not account_id or not namespace_title:
        return {"status": "skipped", "reason": "cloudflare_unavailable"}
    required = {
        "candidate": private_dir / "config.yaml",
        "browsing": private_dir / "browsing-qualification-summary.json",
        "ai": private_dir / "ai-qualification-summary.json",
    }
    if not all(path.is_file() for path in required.values()):
        return {"status": "skipped", "reason": "aggregate_sources_missing"}

    publisher = _publisher(
        token=token,
        account_id=account_id,
        namespace_title=namespace_title,
        key_name=f"{_production_key(project)}.production-metrics-v1",
    )
    try:
        existing = publisher.read()
        state, load_status = parse_metrics_bytes(existing)
        run = build_metrics_run(
            candidate_path=required["candidate"],
            browsing=_load_json(required["browsing"], "browsing metrics"),
            ai=_load_json(required["ai"], "AI metrics"),
            qualification=_optional_json(private_dir / "qualification-pipeline-summary.json"),
            release=_optional_json(private_dir / "release-publication.json"),
            mihomo_matrix=_optional_json(private_dir / "mihomo-validation-matrix.json"),
            promotion_guard=_optional_json(private_dir / "promotion-guard.json"),
            lifecycle=_optional_json(private_dir / "lifecycle-observability.json"),
        )
        next_state = append_metrics_run(state, run)
        content = (
            json.dumps(next_state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        published = publisher.publish(content=content)
    except (OSError, ValueError, json.JSONDecodeError, PublicationError):
        return {"status": "unavailable"}
    return {
        "status": "published",
        "load_status": load_status,
        "bytes": published["bytes"],
        **metrics_summary(next_state),
    }


def render_production_proof_application(
    *,
    candidate: Path,
    audit: Path,
    browsing: Path,
    ai: Path,
    publication_status: str,
    qualification: Path | None = None,
    release: Path | None = None,
    validated_cores: tuple[str, ...] = (),
    validated_cores_report: Path | None = None,
    markdown: Path | None = None,
) -> dict[str, Any]:
    """Build and optionally persist one privacy-safe production proof."""

    explicit = tuple(validated_cores)
    if validated_cores_report is None:
        if not explicit:
            raise ValidationError("production proof requires at least one validated Mihomo core")
        cores = explicit
    else:
        matrix_report = _load_json(validated_cores_report, "Mihomo validation matrix")
        raw = matrix_report.get("validated_cores")
        if (
            not isinstance(raw, list)
            or not raw
            or not all(isinstance(item, str) and item for item in raw)
        ):
            raise ValidationError("Mihomo validation matrix does not contain validated_cores")
        cores = tuple(str(item) for item in raw)
        if explicit and explicit != cores:
            raise ValidationError("explicit validated cores do not match the matrix report")

    proof = build_production_proof(
        candidate_path=candidate,
        audit=_load_json(audit, "post-qualification audit"),
        browsing=_load_json(browsing, "browsing qualification"),
        ai=_load_json(ai, "AI qualification"),
        validated_cores=cores,
        publication_status=publication_status,
        qualification=_load_json(qualification, "qualification pipeline")
        if qualification is not None
        else None,
        release=_load_json(release, "release transaction") if release is not None else None,
    )
    if markdown is not None:
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text(render_production_proof_markdown(proof), encoding="utf-8")
    return proof
