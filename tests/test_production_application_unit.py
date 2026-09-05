from __future__ import annotations

import json
from pathlib import Path

import pytest

from clash_relay.config_loader import ProjectDefinition
from clash_relay.errors import PublicationError, ValidationError
from clash_relay.production_application import (
    _load_json,
    fetch_current_production_config,
    load_ai_qualification_cache_state,
    load_scheduler_history_state,
    persist_ai_qualification_cache,
    persist_scheduler_history,
)


def _project(root: Path) -> ProjectDefinition:
    return ProjectDefinition(
        root=root,
        config={"publishing": {"cloudflare_kv": {"key": "production-config"}}},
        subscriptions_document={},
        subscriptions=(),
        policies={},
        acl4ssr=None,
    )


def test_load_json_accepts_mapping_and_fails_closed_on_invalid_input(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"status":"ok"}', encoding="utf-8")
    assert _load_json(path, "state") == {"status": "ok"}

    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValidationError, match="must be a JSON mapping"):
        _load_json(path, "state")

    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValidationError, match="failed to load state"):
        _load_json(path, "state")

    with pytest.raises(ValidationError, match="failed to load missing"):
        _load_json(tmp_path / "missing.json", "missing")


def test_private_state_loaders_degrade_safely_without_cloudflare_credentials(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    history = tmp_path / "private" / "history.json"
    history_key = tmp_path / "private" / "history.key"
    cache = tmp_path / "private" / "cache.json"
    cache_key = tmp_path / "private" / "cache.key"

    history_result = load_scheduler_history_state(
        project=project,
        output=history,
        fingerprint_key_output=history_key,
        env={},
    )
    cache_result = load_ai_qualification_cache_state(
        project=project,
        output=cache,
        fingerprint_key_output=cache_key,
        env={},
    )

    assert history_result["status"] == "unavailable"
    assert history_result["source"] == "none"
    assert json.loads(history.read_text(encoding="utf-8"))["nodes"] == {}
    assert history_key.read_text(encoding="ascii") == ""
    assert cache_result["status"] == "unavailable"
    assert json.loads(cache.read_text(encoding="utf-8"))["nodes"] == {}
    assert cache_key.read_text(encoding="ascii") == ""
    assert oct(history_key.stat().st_mode & 0o777) == "0o600"
    assert oct(cache_key.stat().st_mode & 0o777) == "0o600"


def test_fetch_current_production_requires_complete_private_credentials(tmp_path: Path) -> None:
    with pytest.raises(PublicationError, match="credentials are required"):
        fetch_current_production_config(
            project=_project(tmp_path),
            output=tmp_path / "production.yaml",
            env={"CLOUDFLARE_API_TOKEN": "token-only"},
        )


def test_derived_state_publishers_skip_missing_invalid_or_unavailable_state(tmp_path: Path) -> None:
    project = _project(tmp_path)
    missing = tmp_path / "missing.json"

    assert persist_ai_qualification_cache(project=project, state=missing, env={}) == {
        "status": "skipped",
        "reason": "state_missing",
    }
    assert persist_scheduler_history(project=project, state=missing, env={}) == {
        "status": "skipped",
        "reason": "cloudflare_unavailable",
    }

    invalid_cache = tmp_path / "cache.json"
    invalid_cache.write_text("not-json", encoding="utf-8")
    assert persist_ai_qualification_cache(project=project, state=invalid_cache, env={}) == {
        "status": "skipped",
        "reason": "state_invalid",
    }

    valid_cache = tmp_path / "cache-valid.json"
    valid_cache.write_text('{"version":1,"nodes":{}}', encoding="utf-8")
    assert persist_ai_qualification_cache(project=project, state=valid_cache, env={}) == {
        "status": "skipped",
        "reason": "cloudflare_unavailable",
    }
