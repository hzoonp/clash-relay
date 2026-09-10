from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from clash_relay.ai_qualification_cache import empty_ai_cache
from clash_relay.config_loader import ProjectDefinition
from clash_relay.errors import PublicationError
from clash_relay.production_application import (
    fetch_current_production_config,
    load_ai_qualification_cache_state,
    load_scheduler_history_state,
    persist_ai_qualification_cache,
    persist_scheduler_history,
)
from clash_relay.scheduler_history import empty_history


def _project(root: Path) -> ProjectDefinition:
    return ProjectDefinition(
        root=root,
        config={"publishing": {"cloudflare_kv": {"key": "production-config"}}},
        subscriptions_document={},
        subscriptions=(),
        policies={},
        acl4ssr=None,
    )


def _env() -> dict[str, str]:
    return {
        "CLOUDFLARE_API_TOKEN": "private-token",
        "CLOUDFLARE_ACCOUNT_ID": "account",
        "CLOUDFLARE_KV_NAMESPACE_TITLE": "namespace",
    }


class _Reader:
    def __init__(self, content: bytes | None) -> None:
        self.content = content

    def read(self) -> bytes | None:
        return self.content


class _FailingReader:
    def read(self) -> bytes | None:
        raise PublicationError("private transport detail")


class _FailingPublisher:
    def publish(self, *, content: bytes) -> dict[str, Any]:
        assert content
        raise PublicationError("private transport detail")


def test_scheduler_history_falls_back_only_after_confirmed_missing_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    legacy = {
        "version": 2,
        "cohort": {"runs": 2, "latency_ema_ms": 120.0, "last_seen_epoch": 100},
        "nodes": {},
    }
    reads: list[str] = []

    def fake_publisher(**kwargs: str) -> _Reader:
        key = kwargs["key_name"]
        reads.append(key)
        if key.endswith("scheduler-state-v3"):
            return _Reader(None)
        if key.endswith("scheduler-state-v2"):
            return _Reader(json.dumps(legacy).encode())
        raise AssertionError(f"unexpected fallback read: {key}")

    monkeypatch.setattr("clash_relay.production_application._publisher", fake_publisher)
    output = tmp_path / "private" / "history.json"
    key_output = tmp_path / "private" / "history.key"

    result = load_scheduler_history_state(
        project=project,
        output=output,
        fingerprint_key_output=key_output,
        env=_env(),
    )

    assert result == {
        "status": "loaded",
        "source": "v2",
        "parse_status": "migrated",
        "state_version": 3,
        "records": 0,
    }
    assert reads == [
        "production-config.scheduler-state-v3",
        "production-config.scheduler-state-v2",
    ]
    assert json.loads(output.read_text(encoding="utf-8"))["version"] == 3
    assert len(key_output.read_text(encoding="ascii").strip()) == 64
    assert oct(key_output.stat().st_mode & 0o777) == "0o600"


def test_scheduler_history_transport_error_does_not_fall_back_to_stale_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[str] = []

    def fake_publisher(**kwargs: str) -> _FailingReader:
        reads.append(kwargs["key_name"])
        return _FailingReader()

    monkeypatch.setattr("clash_relay.production_application._publisher", fake_publisher)
    output = tmp_path / "history.json"
    key_output = tmp_path / "history.key"

    result = load_scheduler_history_state(
        project=_project(tmp_path),
        output=output,
        fingerprint_key_output=key_output,
        env=_env(),
    )

    assert result["status"] == "unavailable"
    assert result["source"] == "none"
    assert result["parse_status"] == "missing"
    assert reads == ["production-config.scheduler-state-v3"]
    assert key_output.read_text(encoding="ascii") == ""


def test_ai_cache_transport_error_degrades_to_empty_private_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "clash_relay.production_application._publisher",
        lambda **_kwargs: _FailingReader(),
    )
    output = tmp_path / "cache.json"
    key_output = tmp_path / "cache.key"

    result = load_ai_qualification_cache_state(
        project=_project(tmp_path),
        output=output,
        fingerprint_key_output=key_output,
        env=_env(),
    )

    assert result == {"status": "unavailable", "parse_status": "missing", "records": 0}
    assert json.loads(output.read_text(encoding="utf-8")) == empty_ai_cache()
    assert key_output.read_text(encoding="ascii") == ""


def test_fetch_current_production_distinguishes_missing_and_empty_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "current.yaml"
    output.write_text("stale", encoding="utf-8")
    monkeypatch.setattr(
        "clash_relay.production_application._publisher",
        lambda **_kwargs: _Reader(None),
    )

    assert fetch_current_production_config(
        project=_project(tmp_path),
        output=output,
        allow_missing=True,
        env=_env(),
    ) == {"status": "absent"}
    assert not output.exists()

    monkeypatch.setattr(
        "clash_relay.production_application._publisher",
        lambda **_kwargs: _Reader(b""),
    )
    with pytest.raises(PublicationError, match="release is empty"):
        fetch_current_production_config(
            project=_project(tmp_path),
            output=output,
            allow_missing=True,
            env=_env(),
        )


def test_derived_state_publication_failures_return_aggregate_safe_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "clash_relay.production_application._publisher",
        lambda **_kwargs: _FailingPublisher(),
    )
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps(empty_ai_cache()), encoding="utf-8")
    history = tmp_path / "history.json"
    history.write_text(json.dumps(empty_history()), encoding="utf-8")
    project = _project(tmp_path)

    assert persist_ai_qualification_cache(project=project, state=cache, env=_env()) == {
        "status": "unavailable",
        "records_preserved": False,
    }
    assert persist_scheduler_history(project=project, state=history, env=_env()) == {
        "status": "unavailable",
        "records_preserved": False,
    }
