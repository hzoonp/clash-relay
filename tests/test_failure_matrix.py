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
    assert "cannot be used to rescue `general`" in matrix


def test_production_publish_remains_after_all_mandatory_gates(repo_root: Path) -> None:
    text = (repo_root / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    publish = text.index("Publish versioned validated release transaction")
    assert text.index("Audit source-to-scenario isolation") < publish
    assert text.index("Qualify private candidate through the unified pipeline") < publish
    assert text.index("Re-audit qualified candidate") < publish
    assert (
        text.index("Validate exact qualified candidate with the pinned stable Mihomo matrix")
        < publish
    )
    assert "--manifest tools/mihomo-versions.json" in text[:publish]
    assert "scripts/snapshot_previous_config.py" not in text
    assert "clash-relay publish-cloudflare-kv" not in text
    assert publish < text.index("Persist private AI qualification cache")
    assert publish < text.index("Persist private scheduler history")


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
        "General inventory/pool",
        "Browsing live qualification",
        "OpenAI has zero qualified nodes",
        "Claude has zero qualified nodes",
        "Gemini has zero qualified nodes",
        "ACL4SSR/rule acquisition",
        "reachability audit",
        "pinned stable Mihomo core",
        "Cloudflare versioned release transaction",
        "Scheduler history",
        "AI qualification cache",
        "Previous release metadata",
        "Rollback candidate",
    ):
        assert phrase in text
