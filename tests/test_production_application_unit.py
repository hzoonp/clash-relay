from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from clash_relay.config_loader import ProjectDefinition
from clash_relay.errors import PublicationError, ValidationError
from clash_relay.production_application import (
    _load_json,
    apply_production_release_retention,
    fetch_current_production_config,
    load_ai_qualification_cache_state,
    load_scheduler_history_state,
    persist_ai_qualification_cache,
    persist_scheduler_history,
    plan_production_release_retention,
    publish_production_release,
    reconcile_production_release,
)
from clash_relay.release_bundle import release_id_for, release_keys


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
    if os.name != "nt":
        assert oct(history_key.stat().st_mode & 0o777) == "0o600"
        assert oct(cache_key.stat().st_mode & 0o777) == "0o600"


def test_fetch_current_production_requires_complete_private_credentials(tmp_path: Path) -> None:
    with pytest.raises(PublicationError, match="credentials are required"):
        fetch_current_production_config(
            project=_project(tmp_path),
            output=tmp_path / "production.yaml",
            env={"CLOUDFLARE_API_TOKEN": "token-only"},
        )


def test_release_reconciliation_requires_private_credentials(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text("candidate\n", encoding="utf-8")

    with pytest.raises(PublicationError, match="credentials are required"):
        reconcile_production_release(
            project=_project(tmp_path),
            candidate=candidate,
            previous=None,
            env={},
        )


def test_release_reconciliation_reads_only_the_versioned_release_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = tmp_path / "previous.yaml"
    candidate = tmp_path / "candidate.yaml"
    previous.write_bytes(b"previous\n")
    candidate.write_bytes(b"candidate\n")
    keys = release_keys("production-config")
    values = {
        keys.production: candidate.read_bytes(),
        keys.current_pointer: f"{release_id_for(candidate.read_bytes())}\n".encode("ascii"),
        keys.previous_pointer: f"{release_id_for(previous.read_bytes())}\n".encode("ascii"),
    }
    calls: list[tuple[str, str]] = []

    class Reader:
        def __init__(self, key: str) -> None:
            self.key = key

        def resolve_namespace_id(self) -> str:
            return "namespace-id"

        def read(self) -> bytes | None:
            calls.append(("read", self.key))
            return values.get(self.key)

        def publish(self, *, content: bytes) -> dict[str, object]:
            raise AssertionError("release reconciliation must not publish")

    monkeypatch.setattr(
        "clash_relay.production_application._publisher",
        lambda **kwargs: Reader(str(kwargs["key_name"])),
    )

    result = reconcile_production_release(
        project=_project(tmp_path),
        candidate=candidate,
        previous=previous,
        env={
            "CLOUDFLARE_API_TOKEN": "private-token",
            "CLOUDFLARE_ACCOUNT_ID": "account",
            "CLOUDFLARE_KV_NAMESPACE_TITLE": "namespace",
        },
    )

    assert result["status"] == "committed"
    assert result["production_changed"] is True
    assert calls == [
        ("read", keys.production),
        ("read", keys.current_pointer),
        ("read", keys.previous_pointer),
    ]


def test_release_retention_plan_reads_journal_and_pointers_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = release_id_for(b"current\n")
    previous = release_id_for(b"previous\n")
    expired = release_id_for(b"expired\n")
    keys = release_keys("production-config")
    values = {
        keys.journal: f'{{"releases":{{"{expired}":{{"first_seen_epoch":0}}}},"version":1}}\n'.encode(
            "ascii"
        ),
        keys.current_pointer: f"{current}\n".encode("ascii"),
        keys.previous_pointer: f"{previous}\n".encode("ascii"),
    }
    calls: list[str] = []

    class Reader:
        def __init__(self, key: str) -> None:
            self.key = key

        def resolve_namespace_id(self) -> str:
            return "namespace-id"

        def read(self) -> bytes | None:
            calls.append(self.key)
            return values.get(self.key)

        def publish(self, *, content: bytes) -> dict[str, object]:
            raise AssertionError("retention planning must not publish")

    monkeypatch.setattr(
        "clash_relay.production_application._publisher",
        lambda **kwargs: Reader(str(kwargs["key_name"])),
    )
    result = plan_production_release_retention(
        project=_project(tmp_path),
        retention_days=0,
        env={
            "CLOUDFLARE_API_TOKEN": "private-token",
            "CLOUDFLARE_ACCOUNT_ID": "account",
            "CLOUDFLARE_KV_NAMESPACE_TITLE": "namespace",
        },
    )

    assert result["mutation"] == "none"
    assert result["deletion_candidate_ids"] == [expired]
    assert calls == [keys.journal, keys.current_pointer, keys.previous_pointer]


def test_committed_release_records_an_append_only_journal_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "candidate.yaml"
    candidate.write_bytes(b"candidate\n")
    release_id = release_id_for(candidate.read_bytes())
    keys = release_keys("production-config")
    values: dict[str, bytes] = {}

    class Publisher:
        def __init__(self, key: str) -> None:
            self.key = key

        def resolve_namespace_id(self) -> str:
            return "namespace-id"

        def read(self) -> bytes | None:
            return values.get(self.key)

        def publish(self, *, content: bytes) -> dict[str, object]:
            values[self.key] = content
            return {"key": self.key, "bytes": len(content)}

    monkeypatch.setattr(
        "clash_relay.production_application._publisher",
        lambda **kwargs: Publisher(str(kwargs["key_name"])),
    )
    monkeypatch.setattr("clash_relay.production_application.publication_gate", lambda *args: None)
    monkeypatch.setattr("clash_relay.production_application.load_candidate", lambda path: {})
    monkeypatch.setattr(
        "clash_relay.production_application.validate_generated_config", lambda value: None
    )
    monkeypatch.setattr(
        "clash_relay.production_application.commit_release_bundle",
        lambda **kwargs: {"status": "published", "release_id": release_id},
    )

    result = publish_production_release(
        project=_project(tmp_path),
        candidate_path=candidate,
        env={
            "CLOUDFLARE_API_TOKEN": "private-token",
            "CLOUDFLARE_ACCOUNT_ID": "account",
            "CLOUDFLARE_KV_NAMESPACE_TITLE": "namespace",
        },
    )

    assert result["release_journal"] == "recorded"
    assert release_id.encode("ascii") in values[keys.journal]


def test_retention_apply_deletes_only_revalidated_candidate_pairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expired = release_id_for(b"expired\n")
    keys = release_keys("production-config")
    plan = {
        "status": "planned",
        "plan_id": "fresh-plan",
        "retention_days": 30,
        "production_key": "production-config",
        "mutation": "none",
    }
    deleted: list[str] = []
    journal_writes: list[bytes] = []

    class Publisher:
        def __init__(self, key: str) -> None:
            self.key = key

        def resolve_namespace_id(self) -> str:
            return "namespace-id"

        def read(self) -> bytes | None:
            if self.key == keys.journal:
                return (
                    f'{{"releases":{{"{expired}":{{"first_seen_epoch":0}}}},"version":1}}'.encode(
                        "ascii"
                    )
                )
            return None

        def publish(self, *, content: bytes) -> dict[str, object]:
            if self.key == keys.journal:
                journal_writes.append(content)
                return {"key": self.key, "bytes": len(content)}
            raise AssertionError("only the journal may be updated after retention deletion")

        def delete(self) -> dict[str, object]:
            deleted.append(self.key)
            return {"key": self.key}

    monkeypatch.setattr(
        "clash_relay.production_application._publisher",
        lambda **kwargs: Publisher(str(kwargs["key_name"])),
    )
    monkeypatch.setattr(
        "clash_relay.production_application.plan_production_release_retention",
        lambda **kwargs: {**plan, "deletion_candidate_ids": [expired]},
    )

    result = apply_production_release_retention(
        project=_project(tmp_path),
        plan=plan,
        env={
            "CLOUDFLARE_API_TOKEN": "private-token",
            "CLOUDFLARE_ACCOUNT_ID": "account",
            "CLOUDFLARE_KV_NAMESPACE_TITLE": "namespace",
        },
    )

    assert result["deleted_release_ids"] == [expired]
    assert deleted == [keys.config(expired), keys.manifest(expired)]
    assert expired.encode("ascii") not in journal_writes[0]


def test_retention_apply_does_not_mutate_journal_after_a_partial_delete_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expired = release_id_for(b"expired\n")
    keys = release_keys("production-config")
    plan = {
        "status": "planned",
        "plan_id": "fresh-plan",
        "retention_days": 30,
        "production_key": "production-config",
        "mutation": "none",
    }
    journal_writes: list[bytes] = []

    class Publisher:
        def __init__(self, key: str) -> None:
            self.key = key

        def resolve_namespace_id(self) -> str:
            return "namespace-id"

        def read(self) -> bytes | None:
            if self.key == keys.journal:
                return (
                    f'{{"releases":{{"{expired}":{{"first_seen_epoch":0}}}},"version":1}}'.encode(
                        "ascii"
                    )
                )
            return None

        def publish(self, *, content: bytes) -> dict[str, object]:
            journal_writes.append(content)
            return {"key": self.key, "bytes": len(content)}

        def delete(self) -> dict[str, object]:
            if self.key == keys.manifest(expired):
                raise PublicationError("simulated delete rejection")
            return {"key": self.key}

    monkeypatch.setattr(
        "clash_relay.production_application._publisher",
        lambda **kwargs: Publisher(str(kwargs["key_name"])),
    )
    monkeypatch.setattr(
        "clash_relay.production_application.plan_production_release_retention",
        lambda **kwargs: {**plan, "deletion_candidate_ids": [expired]},
    )

    with pytest.raises(PublicationError, match="delete rejection"):
        apply_production_release_retention(
            project=_project(tmp_path),
            plan=plan,
            env={
                "CLOUDFLARE_API_TOKEN": "private-token",
                "CLOUDFLARE_ACCOUNT_ID": "account",
                "CLOUDFLARE_KV_NAMESPACE_TITLE": "namespace",
            },
        )

    assert journal_writes == []


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
