from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import pytest

from clash_relay.errors import PublicationError
from clash_relay.release_bundle import (
    manifest_bytes,
    parse_release_pointer,
    publish_release_bundle,
    read_previous_release,
    release_id_for,
    release_keys,
)


@dataclass
class MemoryKV:
    values: dict[str, bytes] = field(default_factory=dict)
    fail_once: set[str] = field(default_factory=set)
    fail_once_message: dict[str, str] = field(default_factory=dict)
    fail_content_once: set[tuple[str, bytes]] = field(default_factory=set)
    ambiguous_once: set[str] = field(default_factory=set)
    fail_always: set[str] = field(default_factory=set)

    def factory(self, key: str):
        store = self

        class Publisher:
            def read(self) -> bytes | None:
                return store.values.get(key)

            def publish(self, *, content: bytes) -> dict:
                if key in store.fail_always:
                    raise PublicationError("simulated persistent write failure")
                if key in store.fail_once_message:
                    raise PublicationError(store.fail_once_message.pop(key))
                if key in store.fail_once:
                    store.fail_once.remove(key)
                    raise PublicationError("simulated write failure")
                content_failure = (key, bytes(content))
                if content_failure in store.fail_content_once:
                    store.fail_content_once.remove(content_failure)
                    raise PublicationError("simulated content-specific write failure")
                store.values[key] = bytes(content)
                if key in store.ambiguous_once:
                    store.ambiguous_once.remove(key)
                    raise PublicationError("simulated lost response after successful write")
                return {
                    "key": key,
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }

        return Publisher()


def test_release_publication_versions_exact_bytes_and_updates_pointers() -> None:
    kv = MemoryKV()
    key = "production-config"
    first = b"version: first\n"
    second = b"version: second\n"

    first_result = publish_release_bundle(factory=kv.factory, production_key=key, content=first)
    second_result = publish_release_bundle(factory=kv.factory, production_key=key, content=second)
    keys = release_keys(key)
    first_id = release_id_for(first)
    second_id = release_id_for(second)

    assert first_result["first_release"] is True
    assert second_result["previous_release_id"] == first_id
    assert kv.values[key] == second
    assert kv.values[keys.config(first_id)] == first
    assert kv.values[keys.config(second_id)] == second
    assert kv.values[keys.current_pointer].decode().strip() == second_id
    assert kv.values[keys.previous_pointer].decode().strip() == first_id
    assert kv.values[keys.legacy_previous] == first

    previous, metadata = read_previous_release(factory=kv.factory, production_key=key)
    assert previous == first
    assert metadata["source"] == "versioned-release"
    assert metadata["release_id"] == first_id


def test_unchanged_refresh_is_idempotent_and_preserves_previous_release() -> None:
    kv = MemoryKV()
    key = "production-config"
    first = b"version: first\n"
    second = b"version: second\n"
    publish_release_bundle(factory=kv.factory, production_key=key, content=first)
    publish_release_bundle(factory=kv.factory, production_key=key, content=second)
    keys = release_keys(key)
    previous_before = kv.values[keys.previous_pointer]

    result = publish_release_bundle(factory=kv.factory, production_key=key, content=second)

    assert result["status"] == "unchanged"
    assert result["production_changed"] is False
    assert result["release_id"] == release_id_for(second)
    assert kv.values[key] == second
    assert kv.values[keys.current_pointer].decode().strip() == release_id_for(second)
    assert kv.values[keys.previous_pointer] == previous_before
    assert kv.values[keys.previous_pointer].decode().strip() == release_id_for(first)


def test_failed_pointer_commit_restores_previous_production_bytes() -> None:
    kv = MemoryKV()
    key = "production-config"
    first = b"version: first\n"
    second = b"version: second\n"
    publish_release_bundle(factory=kv.factory, production_key=key, content=first)
    keys = release_keys(key)
    kv.fail_once.add(keys.current_pointer)

    with pytest.raises(PublicationError, match="previous production bytes were restored"):
        publish_release_bundle(factory=kv.factory, production_key=key, content=second)

    assert kv.values[key] == first
    assert kv.values[keys.current_pointer].decode().strip() == release_id_for(first)


def test_ambiguous_successful_production_put_is_recovered_by_exact_readback() -> None:
    kv = MemoryKV()
    key = "production-config"
    first = b"version: first\n"
    second = b"version: second\n"
    publish_release_bundle(factory=kv.factory, production_key=key, content=first)
    kv.ambiguous_once.add(key)

    result = publish_release_bundle(factory=kv.factory, production_key=key, content=second)

    assert result["status"] == "published"
    assert kv.values[key] == second
    assert parse_release_pointer(kv.values[release_keys(key).current_pointer]) == release_id_for(
        second
    )


def test_ambiguous_successful_pointer_put_is_recovered_by_exact_readback() -> None:
    kv = MemoryKV()
    key = "production-config"
    first = b"version: first\n"
    second = b"version: second\n"
    publish_release_bundle(factory=kv.factory, production_key=key, content=first)
    keys = release_keys(key)
    kv.ambiguous_once.add(keys.current_pointer)

    result = publish_release_bundle(factory=kv.factory, production_key=key, content=second)

    assert result["status"] == "published"
    assert kv.values[key] == second
    assert parse_release_pointer(kv.values[keys.current_pointer]) == release_id_for(second)
    assert parse_release_pointer(kv.values[keys.previous_pointer]) == release_id_for(first)


def test_first_release_activation_failure_restores_empty_pointer_state() -> None:
    kv = MemoryKV()
    key = "production-config"
    keys = release_keys(key)
    kv.fail_once.add(key)

    with pytest.raises(PublicationError, match="current pointer was restored"):
        publish_release_bundle(factory=kv.factory, production_key=key, content=b"first\n")

    assert key not in kv.values
    assert parse_release_pointer(kv.values[keys.current_pointer]) is None


def test_incomplete_compensation_is_reported_explicitly() -> None:
    kv = MemoryKV()
    key = "production-config"
    first = b"first\n"
    second = b"second\n"
    publish_release_bundle(factory=kv.factory, production_key=key, content=first)
    keys = release_keys(key)
    kv.fail_once.add(keys.current_pointer)
    kv.fail_content_once.add((key, first))

    with pytest.raises(PublicationError, match="compensation was incomplete: production"):
        publish_release_bundle(factory=kv.factory, production_key=key, content=second)

    assert kv.values[key] == second


@pytest.mark.parametrize(
    "failure",
    (
        "Cloudflare API request failed with HTTP 429",
        "Cloudflare API request failed with HTTP 503",
        "Cloudflare API request failed",
    ),
)
def test_pre_activation_api_failure_never_changes_client_visible_bytes(failure: str) -> None:
    kv = MemoryKV()
    key = "production-config"
    first = b"first\n"
    second = b"second\n"
    publish_release_bundle(factory=kv.factory, production_key=key, content=first)
    keys = release_keys(key)
    kv.fail_once_message[keys.config(release_id_for(second))] = failure

    with pytest.raises(PublicationError, match="Cloudflare API request failed"):
        publish_release_bundle(factory=kv.factory, production_key=key, content=second)

    assert kv.values[key] == first
    assert parse_release_pointer(kv.values[keys.current_pointer]) == release_id_for(first)


def test_rollback_rehearsal_round_trips_exact_versioned_bytes() -> None:
    kv = MemoryKV()
    key = "production-config"
    first = b"version: first\n"
    second = b"version: second\n"
    publish_release_bundle(factory=kv.factory, production_key=key, content=first)
    publish_release_bundle(factory=kv.factory, production_key=key, content=second)

    previous, metadata = read_previous_release(factory=kv.factory, production_key=key)
    result = publish_release_bundle(factory=kv.factory, production_key=key, content=previous)
    keys = release_keys(key)

    assert metadata["release_id"] == release_id_for(first)
    assert result["status"] == "published"
    assert result["release_id"] == release_id_for(first)
    assert result["previous_release_id"] == release_id_for(second)
    assert kv.values[key] == first
    assert parse_release_pointer(kv.values[keys.current_pointer]) == release_id_for(first)
    assert parse_release_pointer(kv.values[keys.previous_pointer]) == release_id_for(second)
    assert kv.values[keys.manifest(release_id_for(first))] == manifest_bytes(first)
    assert kv.values[keys.manifest(release_id_for(second))] == manifest_bytes(second)


def test_previous_reader_falls_back_to_legacy_slot() -> None:
    kv = MemoryKV()
    key = "production-config"
    legacy = b"legacy: true\n"
    kv.values[release_keys(key).legacy_previous] = legacy
    content, metadata = read_previous_release(factory=kv.factory, production_key=key)
    assert content == legacy
    assert metadata["source"] == "legacy-previous-v1"


def test_previous_reader_rejects_missing_versioned_manifest() -> None:
    kv = MemoryKV()
    key = "production-config"
    content = b"previous\n"
    release_id = release_id_for(content)
    keys = release_keys(key)
    kv.values[keys.previous_pointer] = f"{release_id}\n".encode()
    kv.values[keys.config(release_id)] = content

    with pytest.raises(PublicationError, match="manifest is missing"):
        read_previous_release(factory=kv.factory, production_key=key)


def test_previous_reader_rejects_corrupt_versioned_manifest() -> None:
    kv = MemoryKV()
    key = "production-config"
    content = b"previous\n"
    release_id = release_id_for(content)
    keys = release_keys(key)
    kv.values[keys.previous_pointer] = f"{release_id}\n".encode()
    kv.values[keys.config(release_id)] = content
    kv.values[keys.manifest(release_id)] = manifest_bytes(content) + b"corrupt"

    with pytest.raises(PublicationError, match="manifest does not match"):
        read_previous_release(factory=kv.factory, production_key=key)
