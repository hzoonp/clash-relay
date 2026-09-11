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
class FaultKV:
    values: dict[str, bytes] = field(default_factory=dict)
    fail_once: set[str] = field(default_factory=set)
    fail_always: set[str] = field(default_factory=set)
    fail_content_once: set[tuple[str, bytes]] = field(default_factory=set)

    def factory(self, key: str):
        store = self

        class Publisher:
            def read(self) -> bytes | None:
                return store.values.get(key)

            def publish(self, *, content: bytes) -> dict[str, object]:
                if key in store.fail_always:
                    raise PublicationError("simulated persistent write failure")
                if key in store.fail_once:
                    store.fail_once.remove(key)
                    raise PublicationError("simulated write failure")
                content_failure = (key, bytes(content))
                if content_failure in store.fail_content_once:
                    store.fail_content_once.remove(content_failure)
                    raise PublicationError("simulated content-specific write failure")
                store.values[key] = bytes(content)
                return {
                    "key": key,
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }

        return Publisher()


def test_release_identity_rejects_empty_key_and_empty_content() -> None:
    with pytest.raises(PublicationError, match="key must not be empty"):
        release_keys("")
    with pytest.raises(PublicationError, match="empty production release"):
        release_id_for(b"")


@pytest.mark.parametrize("pointer", [b"not-a-release-id\n", b"\xff\n"])
def test_release_pointer_rejects_invalid_encoding_or_identity(pointer: bytes) -> None:
    with pytest.raises(PublicationError):
        parse_release_pointer(pointer)


def test_existing_immutable_config_collision_fails_before_activation() -> None:
    kv = FaultKV()
    key = "production-config"
    content = b"candidate\n"
    keys = release_keys(key)
    release_id = release_id_for(content)
    kv.values[keys.config(release_id)] = b"different\n"

    with pytest.raises(PublicationError, match="different bytes"):
        publish_release_bundle(factory=kv.factory, production_key=key, content=content)

    assert key not in kv.values


def test_existing_immutable_manifest_collision_fails_before_activation() -> None:
    kv = FaultKV()
    key = "production-config"
    content = b"candidate\n"
    keys = release_keys(key)
    release_id = release_id_for(content)
    kv.values[keys.config(release_id)] = content
    kv.values[keys.manifest(release_id)] = b"wrong-manifest\n"

    with pytest.raises(PublicationError, match="manifest does not match"):
        publish_release_bundle(factory=kv.factory, production_key=key, content=content)

    assert key not in kv.values


def test_unchanged_release_repairs_missing_current_pointer() -> None:
    kv = FaultKV()
    key = "production-config"
    content = b"same\n"
    publish_release_bundle(factory=kv.factory, production_key=key, content=content)
    keys = release_keys(key)
    kv.values.pop(keys.current_pointer)

    result = publish_release_bundle(factory=kv.factory, production_key=key, content=content)

    assert result["status"] == "unchanged"
    assert parse_release_pointer(kv.values[keys.current_pointer]) == release_id_for(content)


def test_first_release_reports_incomplete_pointer_compensation() -> None:
    kv = FaultKV()
    key = "production-config"
    keys = release_keys(key)
    kv.fail_once.add(key)
    kv.fail_content_once.add((keys.current_pointer, b"\n"))

    with pytest.raises(PublicationError, match="pointer compensation was incomplete"):
        publish_release_bundle(factory=kv.factory, production_key=key, content=b"first\n")


def test_failed_commit_reports_current_pointer_compensation_failure() -> None:
    kv = FaultKV()
    key = "production-config"
    first = b"first\n"
    second = b"second\n"
    keys = release_keys(key)
    # Model a legacy production value whose versioned current pointer has not yet
    # been migrated. That makes the compensation write observable instead of
    # already matching the expected old release id before the write is attempted.
    kv.values[key] = first
    kv.fail_once.add(keys.previous_pointer)
    kv.fail_content_once.add((keys.current_pointer, f"{release_id_for(first)}\n".encode()))

    with pytest.raises(PublicationError, match="compensation was incomplete: current-pointer"):
        publish_release_bundle(factory=kv.factory, production_key=key, content=second)


def test_failed_commit_reports_previous_pointer_compensation_failure() -> None:
    kv = FaultKV()
    key = "production-config"
    first = b"first\n"
    second = b"second\n"
    publish_release_bundle(factory=kv.factory, production_key=key, content=first)
    keys = release_keys(key)
    kv.fail_once.add(keys.current_pointer)
    kv.fail_always.add(keys.previous_pointer)

    with pytest.raises(PublicationError, match="compensation was incomplete: previous-pointer"):
        publish_release_bundle(factory=kv.factory, production_key=key, content=second)


def test_previous_reader_rejects_missing_release_bytes() -> None:
    kv = FaultKV()
    key = "production-config"
    content = b"previous\n"
    release_id = release_id_for(content)
    keys = release_keys(key)
    kv.values[keys.previous_pointer] = f"{release_id}\n".encode()

    with pytest.raises(PublicationError, match="missing release"):
        read_previous_release(factory=kv.factory, production_key=key)


def test_previous_reader_rejects_bytes_that_do_not_match_pointer_identity() -> None:
    kv = FaultKV()
    key = "production-config"
    expected = b"previous\n"
    release_id = release_id_for(expected)
    keys = release_keys(key)
    kv.values[keys.previous_pointer] = f"{release_id}\n".encode()
    kv.values[keys.config(release_id)] = b"tampered\n"
    kv.values[keys.manifest(release_id)] = manifest_bytes(expected)

    with pytest.raises(PublicationError, match="bytes do not match"):
        read_previous_release(factory=kv.factory, production_key=key)
