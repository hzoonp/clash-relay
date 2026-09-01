"""Versioned private release bundles and compensating publication semantics.

Mihomo/FlClash consumers continue reading the configured production KV key.
P17 stages immutable releases first, verifies exact bytes, then updates that
compatibility key and commits release pointers. If a pointer commit fails after
production changes, the previous exact bytes are restored when available.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .errors import PublicationError

_RELEASE_ID = re.compile(r"^[0-9a-f]{64}$")
_READ_BACK_DELAYS = (0.0, 0.25, 0.5, 1.0, 2.0)


class KVValue(Protocol):
    def read(self) -> bytes | None: ...

    def publish(self, *, content: bytes) -> dict[str, Any]: ...


PublisherFactory = Callable[[str], KVValue]


@dataclass(frozen=True, slots=True)
class ReleaseKeys:
    production: str
    current_pointer: str
    previous_pointer: str
    legacy_previous: str

    def config(self, release_id: str) -> str:
        _validate_release_id(release_id)
        return f"{self.production}.release-v1.{release_id}.config"

    def manifest(self, release_id: str) -> str:
        _validate_release_id(release_id)
        return f"{self.production}.release-v1.{release_id}.manifest"


def release_keys(production_key: str) -> ReleaseKeys:
    if not production_key:
        raise PublicationError("production release key must not be empty")
    return ReleaseKeys(
        production=production_key,
        current_pointer=f"{production_key}.current-release-v1",
        previous_pointer=f"{production_key}.previous-release-v1",
        legacy_previous=f"{production_key}.previous-v1",
    )


def release_id_for(content: bytes) -> str:
    if not content:
        raise PublicationError("refusing to create an empty production release")
    return hashlib.sha256(content).hexdigest()


def _validate_release_id(value: str) -> None:
    if _RELEASE_ID.fullmatch(value) is None:
        raise PublicationError("production release pointer contains an invalid release id")


def _pointer_bytes(release_id: str) -> bytes:
    _validate_release_id(release_id)
    return f"{release_id}\n".encode("ascii")


def parse_release_pointer(content: bytes | None) -> str | None:
    if content is None:
        return None
    try:
        value = content.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise PublicationError("production release pointer is not ASCII") from exc
    if not value:
        return None
    _validate_release_id(value)
    return value


def manifest_bytes(content: bytes) -> bytes:
    release_id = release_id_for(content)
    document = {
        "schema_version": 1,
        "release_id": release_id,
        "sha256": release_id,
        "bytes": len(content),
    }
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _matches_after_write(publisher: KVValue, expected: bytes) -> bool:
    """Retry short read-after-write propagation without weakening byte equality."""

    for delay in _READ_BACK_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            observed = publisher.read()
        except PublicationError:
            continue
        if observed == expected:
            return True
    return False


def _publish_verified(factory: PublisherFactory, key: str, content: bytes) -> dict[str, Any]:
    publisher = factory(key)
    try:
        result = publisher.publish(content=content)
    except PublicationError:
        # A remote PUT may succeed while its response is lost. Read back before
        # declaring failure so an ambiguous network response cannot create a
        # false "failed but production changed" result.
        if not _matches_after_write(publisher, content):
            raise
        result = {
            "key": key,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "recovered_from_ambiguous_write": True,
        }
    if not _matches_after_write(publisher, content):
        raise PublicationError(f"Cloudflare KV read-back verification failed for {key!r}")
    return result


def _ensure_immutable_release(
    factory: PublisherFactory,
    keys: ReleaseKeys,
    content: bytes,
) -> str:
    release_id = release_id_for(content)
    config_key = keys.config(release_id)
    existing = factory(config_key).read()
    if existing is None:
        _publish_verified(factory, config_key, content)
    elif existing != content:
        raise PublicationError("immutable production release key contains different bytes")

    expected_manifest = manifest_bytes(content)
    manifest_key = keys.manifest(release_id)
    existing_manifest = factory(manifest_key).read()
    if existing_manifest is None:
        _publish_verified(factory, manifest_key, expected_manifest)
    elif existing_manifest != expected_manifest:
        raise PublicationError(
            "immutable production release manifest does not match release bytes"
        )
    return release_id


def _safe_read(factory: PublisherFactory, key: str) -> bytes | None:
    return factory(key).read()


def _restore_after_failed_commit(
    factory: PublisherFactory,
    keys: ReleaseKeys,
    *,
    previous_content: bytes,
    previous_release_id: str,
    previous_pointer_before: str | None,
) -> None:
    errors: list[str] = []
    try:
        _publish_verified(factory, keys.production, previous_content)
    except PublicationError:
        errors.append("production")
    try:
        _publish_verified(factory, keys.current_pointer, _pointer_bytes(previous_release_id))
    except PublicationError:
        errors.append("current-pointer")
    restore_previous = previous_pointer_before or previous_release_id
    try:
        _publish_verified(factory, keys.previous_pointer, _pointer_bytes(restore_previous))
    except PublicationError:
        errors.append("previous-pointer")
    if errors:
        raise PublicationError(
            "release commit failed and compensation was incomplete: " + ", ".join(errors)
        )


def publish_release_bundle(
    *,
    factory: PublisherFactory,
    production_key: str,
    content: bytes,
) -> dict[str, Any]:
    """Stage, verify, activate, and commit a versioned release.

    The configured production key remains the client-facing compatibility
    surface. Release objects and pointers are private operational metadata.
    """

    keys = release_keys(production_key)
    new_release_id = _ensure_immutable_release(factory, keys, content)
    current_content = _safe_read(factory, keys.production)
    current_pointer_before = parse_release_pointer(_safe_read(factory, keys.current_pointer))
    previous_pointer_before = parse_release_pointer(_safe_read(factory, keys.previous_pointer))

    if current_content == content:
        if current_pointer_before != new_release_id:
            _publish_verified(factory, keys.current_pointer, _pointer_bytes(new_release_id))
        return {
            "status": "unchanged",
            "release_id": new_release_id,
            "previous_release_id": previous_pointer_before,
            "bytes": len(content),
            "sha256": new_release_id,
            "production_changed": False,
        }

    if current_content is None:
        # First publication has no state to compensate back to. Commit the
        # internal pointer first, then expose the compatibility key. A failed
        # production write leaves no client-visible partial replacement.
        _publish_verified(factory, keys.current_pointer, _pointer_bytes(new_release_id))
        _publish_verified(factory, keys.production, content)
        return {
            "status": "published",
            "release_id": new_release_id,
            "previous_release_id": None,
            "bytes": len(content),
            "sha256": new_release_id,
            "production_changed": True,
            "first_release": True,
        }

    old_release_id = _ensure_immutable_release(factory, keys, current_content)
    # Preserve the legacy recovery slot while old clients/workflows are phased
    # out. It is not used as the primary P17 rollback source.
    _publish_verified(factory, keys.legacy_previous, current_content)

    try:
        _publish_verified(factory, keys.production, content)
        _publish_verified(factory, keys.previous_pointer, _pointer_bytes(old_release_id))
        _publish_verified(factory, keys.current_pointer, _pointer_bytes(new_release_id))
    except PublicationError as exc:
        try:
            _restore_after_failed_commit(
                factory,
                keys,
                previous_content=current_content,
                previous_release_id=old_release_id,
                previous_pointer_before=previous_pointer_before,
            )
        except PublicationError as compensation_error:
            raise compensation_error from exc
        raise PublicationError(
            "release commit failed; previous production bytes were restored"
        ) from exc

    return {
        "status": "published",
        "release_id": new_release_id,
        "previous_release_id": old_release_id,
        "bytes": len(content),
        "sha256": new_release_id,
        "production_changed": True,
        "migrated_current_pointer": current_pointer_before is None,
    }


def read_previous_release(
    *,
    factory: PublisherFactory,
    production_key: str,
) -> tuple[bytes, dict[str, Any]]:
    """Read the versioned previous release, falling back to the legacy slot."""

    keys = release_keys(production_key)
    previous_release_id = parse_release_pointer(_safe_read(factory, keys.previous_pointer))
    if previous_release_id is not None:
        content = _safe_read(factory, keys.config(previous_release_id))
        if content is None:
            raise PublicationError("previous release pointer references a missing release")
        if release_id_for(content) != previous_release_id:
            raise PublicationError(
                "previous release bytes do not match their immutable release id"
            )
        return content, {
            "source": "versioned-release",
            "release_id": previous_release_id,
            "sha256": previous_release_id,
            "bytes": len(content),
        }

    legacy = _safe_read(factory, keys.legacy_previous)
    if legacy is None:
        raise PublicationError("no previous production release is available")
    legacy_release_id = release_id_for(legacy)
    return legacy, {
        "source": "legacy-previous-v1",
        "release_id": legacy_release_id,
        "sha256": legacy_release_id,
        "bytes": len(legacy),
    }
