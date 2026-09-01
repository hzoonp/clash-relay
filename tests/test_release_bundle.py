from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import pytest

from clash_relay.errors import PublicationError
from clash_relay.release_bundle import (
    publish_release_bundle,
    read_previous_release,
    release_id_for,
    release_keys,
)


@dataclass
class MemoryKV:
    values: dict[str, bytes] = field(default_factory=dict)
    fail_once: set[str] = field(default_factory=set)

    def factory(self, key: str):
        store = self

        class Publisher:
            def read(self) -> bytes | None:
                return store.values.get(key)

            def publish(self, *, content: bytes) -> dict:
                if key in store.fail_once:
                    store.fail_once.remove(key)
                    raise PublicationError("simulated write failure")
                store.values[key] = bytes(content)
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


def test_previous_reader_falls_back_to_legacy_slot() -> None:
    kv = MemoryKV()
    key = "production-config"
    legacy = b"legacy: true\n"
    kv.values[release_keys(key).legacy_previous] = legacy
    content, metadata = read_previous_release(factory=kv.factory, production_key=key)
    assert content == legacy
    assert metadata["source"] == "legacy-previous-v1"
