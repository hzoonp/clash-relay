from __future__ import annotations

from pathlib import Path

from clash_relay.ai_qualification_cache import empty_ai_cache, parse_ai_cache_bytes
from clash_relay.scheduler_history import empty_history, parse_history_bytes


def test_auxiliary_state_corruption_falls_back_to_empty_state() -> None:
    history, history_status = parse_history_bytes(b"not-json")
    ai_cache, ai_status = parse_ai_cache_bytes(b"not-json")

    assert history_status == "invalid"
    assert history == empty_history()
    assert ai_status == "invalid"
    assert ai_cache == empty_ai_cache()


def test_builder_preserves_optional_source_failure_thresholds(repo_root: Path) -> None:
    text = (repo_root / "src" / "clash_relay" / "builder.py").read_text(encoding="utf-8")
    assert 'spec.on_error == "fail"' in text
    assert 'generation["minimum_successful_subscriptions"]' in text
    assert 'generation["minimum_usable_nodes"]' in text
    assert "subscription contains no usable proxies" in text


def test_canonical_sources_are_optional_but_degradation_cannot_relax_permissions(
    repo_root: Path,
) -> None:
    text = (repo_root / "subscriptions.yaml").read_text(encoding="utf-8")
    assert "on_error: skip" in text
    assert "allowed_uses: [browsing, ai]" in text
    assert "max_node_multiplier: 2.0" in text
    matrix = (repo_root / "docs" / "failure-matrix.md").read_text(encoding="utf-8")
    assert "Degradation is not permission escalation" in matrix
    assert "cannot rescue `general`" in matrix


def test_production_publish_remains_after_all_mandatory_gates(repo_root: Path) -> None:
    workflow = (repo_root / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    lifecycle = (repo_root / "src" / "clash_relay" / "production_lifecycle.py").read_text(
        encoding="utf-8"
    )
    release_stage = (repo_root / "src" / "clash_relay" / "production_release_stage.py").read_text(
        encoding="utf-8"
    )

    assert workflow.count("python scripts/run_production_release.py") == 1
    assert "check_promotion_guard.py" not in workflow
    assert "validate_mihomo_matrix.py" not in workflow
    assert "publish_release_bundle.py" not in workflow

    qualify = lifecycle.index("pipeline = self._qualify(binary)")
    release_boundary = lifecycle.index(
        "release_stage = self._release_candidate_stage(project, binary)"
    )
    persist = lifecycle.index("derived_state = self._persist_derived_state(project)")
    assert qualify < release_boundary < persist
    assert "run_production_pipeline(" in lifecycle[:release_boundary]
    assert "run_release_candidate_stage(" in lifecycle

    wrapper_start = lifecycle.index("    def _release_candidate_stage(")
    wrapper_end = lifecycle.index("    def _best_effort_state", wrapper_start)
    assert "run_release_candidate_stage(" in lifecycle[wrapper_start:wrapper_end]

    guard = release_stage.index("run_promotion_guard(")
    matrix = release_stage.index("validate_mihomo_matrix(")
    publish = release_stage.index("publish_production_release(")
    assert guard < matrix < publish
    assert "qualification_path=paths.qualification" in release_stage[:matrix]
    assert "check_promotion_guard.py" not in release_stage
    assert "validate_mihomo_matrix.py" not in release_stage
    assert "publish_release_bundle.py" not in release_stage
    assert "scripts/snapshot_previous_config.py" not in release_stage
    assert "clash-relay publish-cloudflare-kv" not in release_stage


def test_rollback_is_confirmed_current_policy_matrix_validated_and_fail_closed(
    repo_root: Path,
) -> None:
    text = (repo_root / ".github" / "workflows" / "rollback.yml").read_text(encoding="utf-8")
    activate = text.index("Activate audited validated previous release")
    audit = text.index("Audit previous release against current production policy")
    validate = text.index("Validate previous release with pinned stable Mihomo matrix")

    assert "inputs.confirm == true" in text
    assert "Fetch private previous release" in text
    assert audit < validate < activate
    assert "--manifest tools/mihomo-versions.json" in text[validate:activate]
    assert "python scripts/publish_release_bundle.py" in text[activate:]
    assert "if: always()" in text


def test_documented_failure_matrix_covers_every_public_failure_class(repo_root: Path) -> None:
    text = (repo_root / "docs" / "failure-matrix.md").read_text(encoding="utf-8")
    for phrase in (
        "One optional subscription",
        "General pool has no eligible nodes",
        "Browsing or transport qualification leaves required automatic inventory empty",
        "OpenAI has zero qualified nodes",
        "Claude has zero qualified nodes",
        "Gemini has zero qualified nodes",
        "ACL4SSR/rule acquisition",
        "Source-to-scenario or Routing V2 audit",
        "Any stable Mihomo core declared in `tools/mihomo-versions.json`",
        "Immutable release PUT succeeds but response is lost",
        "Scheduler history",
        "AI qualification cache",
        "Versioned previous-release pointer is absent",
        "Historical rollback candidate",
    ):
        assert phrase in text
