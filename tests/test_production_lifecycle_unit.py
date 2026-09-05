from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, cast

import pytest

from clash_relay.errors import ValidationError
from clash_relay.production_lifecycle import (
    ProductionLifecyclePaths,
    ProductionPipeline,
    resolve_publication_mode,
)


def _pipeline(tmp_path: Path, *, publish: bool = False) -> ProductionPipeline:
    return ProductionPipeline(ProductionLifecyclePaths.canonical(tmp_path), publish=publish)


def test_publication_mode_resolves_all_supported_event_paths() -> None:
    assert resolve_publication_mode(explicit_publish=True) is True
    assert resolve_publication_mode(explicit_publish=False, event_name="push") is False
    assert resolve_publication_mode(event_name=None) is False
    assert resolve_publication_mode(event_name="push") is True
    assert resolve_publication_mode(event_name="schedule") is True
    assert resolve_publication_mode(event_name="workflow_dispatch", manual_publish=True) is True
    assert resolve_publication_mode(event_name="workflow_dispatch", manual_publish=False) is False
    assert resolve_publication_mode(event_name="workflow_dispatch", manual_publish=" TRUE ") is True
    assert resolve_publication_mode(event_name="workflow_dispatch", manual_publish="false") is False

    with pytest.raises(ValidationError, match="unsupported production publication event"):
        resolve_publication_mode(event_name="pull_request")


def test_lifecycle_json_helpers_round_trip_and_fail_closed(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    target = tmp_path / "state.json"

    pipeline._write_json(target, {"status": "ok", "count": 2})
    assert pipeline._load_json(target) == {"count": 2, "status": "ok"}

    target.write_text("not-json", encoding="utf-8")
    with pytest.raises(ValidationError, match="could not read"):
        pipeline._load_json(target)

    target.write_text("[]", encoding="utf-8")
    with pytest.raises(ValidationError, match="must be an object"):
        pipeline._load_json(target)


def test_best_effort_state_records_safe_warning(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)

    def fail() -> dict[str, Any]:
        raise ValueError("sensitive implementation detail")

    result = pipeline._best_effort_state("persist_optional_state", fail)

    assert result == {"status": "unavailable", "reason": "stage_failed"}
    assert pipeline.warnings == ["persist_optional_state"]


def test_candidate_slo_identity_prefers_qualified_candidate(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    pipeline.paths.private_dir.mkdir(parents=True)
    pipeline._private("generated.yaml").write_bytes(b"generated-candidate")

    generated_sha = hashlib.sha256(b"generated-candidate").hexdigest()
    assert pipeline._candidate_slo_identity() == (generated_sha, len(b"generated-candidate"))

    pipeline._private("config.yaml").write_bytes(b"qualified-candidate")
    qualified_sha = hashlib.sha256(b"qualified-candidate").hexdigest()
    assert pipeline._candidate_slo_identity() == (qualified_sha, len(b"qualified-candidate"))


def test_candidate_slo_identity_handles_missing_or_empty_files(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    pipeline.paths.private_dir.mkdir(parents=True)
    pipeline._private("generated.yaml").write_bytes(b"")

    assert pipeline._candidate_slo_identity() == (None, None)


def test_qualification_retry_state_is_strict_about_attempt_count(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    pipeline.paths.private_dir.mkdir(parents=True)

    assert pipeline._qualification_retry_state() == (False, False)

    pipeline._write_json(
        pipeline._private("qualification-pipeline-summary.json"),
        {"browsing": {"stage_attempts": 2, "recovered_by_retry": True}},
    )
    assert pipeline._qualification_retry_state() == (True, True)

    pipeline._write_json(
        pipeline._private("qualification-pipeline-summary.json"),
        {"browsing": {"stage_attempts": True, "recovered_by_retry": True}},
    )
    assert pipeline._qualification_retry_state() == (False, True)


def test_qualification_retry_state_fails_closed_on_bad_summary(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    pipeline.paths.private_dir.mkdir(parents=True)
    path = pipeline._private("qualification-pipeline-summary.json")

    path.write_text("not-json", encoding="utf-8")
    assert pipeline._qualification_retry_state() == (False, False)

    pipeline._write_json(path, {"browsing": "invalid"})
    assert pipeline._qualification_retry_state() == (False, False)


def test_promotion_slo_state_distinguishes_passed_blocked_and_unknown(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    pipeline.paths.private_dir.mkdir(parents=True)
    report = pipeline._private("promotion-guard.json")

    assert pipeline._promotion_slo_state() == (False, False)

    pipeline._write_json(report, {"status": "passed"})
    assert pipeline._promotion_slo_state() == (True, False)

    pipeline._write_json(report, {"status": "blocked"})
    assert pipeline._promotion_slo_state() == (True, True)

    pipeline._write_json(report, {"status": "unknown"})
    assert pipeline._promotion_slo_state() == (False, False)

    report.write_text("not-json", encoding="utf-8")
    assert pipeline._promotion_slo_state() == (False, False)


def test_dry_run_operational_slo_does_not_touch_external_state(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path, publish=False)

    result = pipeline._record_operational_slo(
        project=cast(Any, None),
        outcome=cast(Any, None),
        lifecycle_started=0.0,
    )

    assert result == {"status": "skipped", "reason": "dry_run"}


def test_post_commit_observability_is_best_effort_only_after_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = _pipeline(tmp_path, publish=True)

    def fail_proof(*, release: dict[str, Any] | None) -> dict[str, Any]:
        del release
        raise ValidationError("private proof detail")

    def fail_manifest(
        *,
        promotion: dict[str, Any],
        matrix: dict[str, Any],
        release: dict[str, Any] | None,
    ) -> dict[str, Any]:
        del promotion, matrix, release
        raise ValidationError("private manifest detail")

    monkeypatch.setattr(published, "_render_existing_proof", fail_proof)
    monkeypatch.setattr(published, "_render_release_manifest", fail_manifest)

    assert published._post_commit_proof(release=None) == {
        "status": "unavailable",
        "reason": "post_commit_observability_failed",
    }
    assert published._post_commit_manifest(promotion={}, matrix={}, release=None) is None
    assert published.warnings == ["render_production_proof", "render_release_manifest"]

    dry_run = _pipeline(tmp_path / "dry-run", publish=False)
    monkeypatch.setattr(dry_run, "_render_existing_proof", fail_proof)
    with pytest.raises(ValidationError, match="private proof detail"):
        dry_run._post_commit_proof(release=None)


def test_run_skips_when_canonical_declarations_are_missing(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)

    assert pipeline.run() == {
        "status": "skipped",
        "publication_status": "not_applicable",
        "reason": "canonical_declarations_missing",
    }
