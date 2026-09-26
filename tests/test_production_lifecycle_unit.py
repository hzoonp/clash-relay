from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, cast

import pytest

from clash_relay.errors import ValidationError
from clash_relay.production_lifecycle import ProductionLifecyclePaths, ProductionPipeline
from clash_relay.runtime_names import runtime_source_label


def _pipeline(tmp_path: Path, *, publish: bool = False) -> ProductionPipeline:
    return ProductionPipeline(ProductionLifecyclePaths.canonical(tmp_path), publish=publish)


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


def test_safe_source_admission_summary_is_aggregate_and_fail_closed(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    pipeline.paths.private_dir.mkdir(parents=True)

    assert pipeline._safe_source_admission_summary() is None

    report = pipeline._private("build-report.json")
    report.write_text("not-json", encoding="utf-8")
    assert pipeline._safe_source_admission_summary() is None

    pipeline._write_json(report, {"subscriptions": "invalid"})
    assert pipeline._safe_source_admission_summary() is None

    pipeline._write_json(
        report,
        {
            "successful_subscriptions": 2,
            "parsed_nodes": 130,
            "usable_nodes": 129,
            "name_filtered_nodes": 3,
            "multiplier_filtered_nodes": 7,
            "subscriptions": [
                {
                    "id": "subscription_1",
                    "status": "failed",
                    "nodes": 4,
                    "skipped_invalid_nodes": 1,
                    "filtered_by_name": 3,
                    "filtered_over_multiplier": 7,
                    "max_node_multiplier": 2.0,
                    "failure_category": "subscription_fetch",
                    "failure_reason": "http_error",
                    "error": "private-fetch-detail",
                },
                "invalid-row",
            ],
        },
    )

    assert pipeline._safe_source_admission_summary() == {
        "successful_subscriptions": 2,
        "parsed_nodes": 130,
        "usable_nodes": 129,
        "name_filtered_nodes": 3,
        "multiplier_filtered_nodes": 7,
        "subscriptions": [
            {
                "id": "subscription_1",
                "status": "failed",
                "nodes": 4,
                "skipped_invalid_nodes": 1,
                "filtered_by_name": 3,
                "filtered_over_multiplier": 7,
                "max_node_multiplier": 2.0,
                "failure_category": "subscription_fetch",
                "failure_reason": "http_error",
            }
        ],
    }


def test_source_stage_accounting_is_safe_and_qualification_aware(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    pipeline.paths.private_dir.mkdir(parents=True)

    pipeline._write_json(
        pipeline._private("production-audit.json"),
        {
            "subscriptions": [
                {
                    "id": "subscription_4",
                    "input_nodes": 12,
                    "parsed_valid_nodes": 11,
                    "skipped_invalid_nodes": 1,
                    "filtered_by_name": 2,
                    "filtered_over_multiplier": 1,
                    "post_multiplier_filter_nodes": 8,
                    "post_dedup_nodes": 7,
                    "runtime_nodes": 6,
                    "server": "private.example",
                }
            ]
        },
    )
    pipeline._write_json(
        pipeline._private("post-qualification-audit.json"),
        {"subscriptions": [{"id": "subscription_4", "runtime_nodes": 2}]},
    )
    pipeline._write_json(
        pipeline._private("qualification-pipeline-summary.json"),
        {
            "qualification_removed_unique_nodes": 4,
            "qualification_removed_runtime_entries": 5,
            "removed_by_stage": {
                "browsing": {
                    "unique_nodes": {"before": 6, "after": 3, "removed": 3},
                    "runtime_entries": {"before": 6, "after": 2, "removed": 4},
                    "by_source": {"sub_4": 4},
                    "unique_by_source": {"sub_4": 3},
                    "failure_category": {"browsing_qualification_failed": 4},
                },
                "service_hardening": {
                    "unique_nodes": {"before": 3, "after": 2, "removed": 1},
                    "runtime_entries": {"before": 2, "after": 2, "removed": 1, "added": 1},
                    "by_source": {"sub_4": 1},
                    "unique_by_source": {"sub_4": 1},
                    "added_by_source": {"sub_4": 1},
                    "failure_category": {"service_client_path_hardening": 1},
                },
            },
            "sources_fully_removed": [],
        },
    )

    assert pipeline._source_stage_accounting() == [
        {
            "id": "subscription_4",
            "input_nodes": 12,
            "parsed_valid_nodes": 11,
            "skipped_invalid_nodes": 1,
            "filtered_by_name": 2,
            "filtered_over_multiplier": 1,
            "post_filter_nodes": 8,
            "post_dedup_nodes": 7,
            "generated_runtime_entries": 6,
            "removed_runtime_entries": 5,
            "added_runtime_entries": 1,
            "final_runtime_entries": 2,
            "removed_unique_nodes": 4,
            "removed_at_stage": None,
            "by_stage": {"browsing": 4, "service_hardening": 1},
        }
    ]
    assert "private.example" not in repr(pipeline._source_stage_accounting())


@pytest.mark.parametrize("source_id", ["subscription_20", "premium-jp"])
def test_fully_removed_source_uses_canonical_id_and_stage(tmp_path: Path, source_id: str) -> None:
    pipeline = _pipeline(tmp_path)
    pipeline.paths.private_dir.mkdir(parents=True)
    label = runtime_source_label(source_id)
    pipeline._write_json(
        pipeline._private("production-audit.json"),
        {"subscriptions": [{"id": source_id, "runtime_nodes": 2}]},
    )
    pipeline._write_json(
        pipeline._private("post-qualification-audit.json"),
        {"subscriptions": [{"id": source_id, "runtime_nodes": 0}]},
    )
    pipeline._write_json(
        pipeline._private("qualification-pipeline-summary.json"),
        {
            "qualification_removed_unique_nodes": 1,
            "qualification_removed_runtime_entries": 2,
            "removed_by_stage": {
                "browsing": {
                    "unique_nodes": {"before": 1, "after": 0, "removed": 1},
                    "runtime_entries": {"before": 2, "after": 0, "removed": 2},
                    "by_source": {label: 2},
                    "unique_by_source": {label: 1},
                    "failure_category": {"browsing_qualification_failed": 2},
                }
            },
            "sources_fully_removed": [
                {
                    "source": label,
                    "unique_nodes": 1,
                    "final_unique_nodes": 0,
                    "runtime_entries": 2,
                    "final_runtime_entries": 0,
                    "removed_at_stage": "browsing",
                    "by_stage": {"browsing": 2},
                    "by_failure_category": {"browsing_qualification_failed": 2},
                }
            ],
        },
    )
    result = pipeline._source_stage_accounting()
    assert result[0]["id"] == source_id
    assert result[0]["removed_at_stage"] == "browsing"
    assert result[0]["removed_unique_nodes"] == 1
    assert result[0]["removed_runtime_entries"] == 2
    assert result[0]["by_stage"] == {"browsing": 2}


def test_dry_run_operational_slo_does_not_touch_external_state(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path, publish=False)

    result = pipeline._record_operational_slo(
        project=cast(Any, None),
        outcome=cast(Any, None),
        lifecycle_started=0.0,
    )

    assert result == {"status": "skipped", "reason": "dry_run"}


def test_dry_run_scheduler_observation_does_not_touch_external_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _pipeline(tmp_path, publish=False)

    def forbidden_publish(*_args, **_kwargs):
        raise AssertionError("dry-run must not call scheduler observation publication")

    monkeypatch.setattr(
        "clash_relay.production_lifecycle.publish_scheduler_observation",
        forbidden_publish,
    )

    result = pipeline._publish_scheduler_observation(
        cast(Any, None),
        metrics={"status": "published"},
    )

    assert result == {"status": "skipped", "reason": "dry_run"}
    assert pipeline.warnings == []


def test_scheduler_observation_requires_freshly_published_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _pipeline(tmp_path, publish=True)

    def forbidden_publish(*_args, **_kwargs):
        raise AssertionError("stale metrics must not produce scheduler evidence")

    monkeypatch.setattr(
        "clash_relay.production_lifecycle.publish_scheduler_observation",
        forbidden_publish,
    )

    result = pipeline._publish_scheduler_observation(
        cast(Any, None),
        metrics={"status": "unavailable"},
    )

    assert result == {"status": "skipped", "reason": "production_metrics_not_published"}


def test_scheduler_observation_is_lifecycle_owned_and_best_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _pipeline(tmp_path, publish=True)
    pipeline.paths.private_dir.mkdir(parents=True)

    monkeypatch.setattr(
        "clash_relay.production_lifecycle.publish_scheduler_observation",
        lambda **_kwargs: {"status": "published", "sample_runs": 3},
    )

    result = pipeline._publish_scheduler_observation(
        cast(Any, None),
        metrics={"status": "published"},
    )

    assert result == {"status": "published", "sample_runs": 3}
    assert pipeline._load_json(pipeline._private("scheduler-observation-publish.json")) == result


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
