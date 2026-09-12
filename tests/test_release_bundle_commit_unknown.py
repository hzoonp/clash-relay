from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import pytest

from clash_relay.errors import CommitUnknownError, PublicationError
from clash_relay.production_diagnostics import safe_failure_diagnostic
from clash_relay.release_bundle import (
    parse_release_pointer,
    publish_release_bundle,
    release_id_for,
    release_keys,
)


@dataclass
class AmbiguousKV:
    values: dict[str, bytes] = field(default_factory=dict)
    definite_fail_once: set[str] = field(default_factory=set)
    ambiguous_once: set[str] = field(default_factory=set)
    visible_ambiguous_once: set[str] = field(default_factory=set)
    visible_ambiguous_read_error_once: set[str] = field(default_factory=set)
    stale_success_once: set[str] = field(default_factory=set)
    stale_reads: dict[str, list[object]] = field(default_factory=dict)
    read_errors: dict[str, int] = field(default_factory=dict)
    writes: list[tuple[str, bytes]] = field(default_factory=list)

    def factory(self, key: str):
        store = self

        class Publisher:
            def read(self) -> bytes | None:
                remaining_errors = store.read_errors.get(key, 0)
                if remaining_errors > 0:
                    store.read_errors[key] = remaining_errors - 1
                    raise PublicationError("simulated transient read failure")
                stale = store.stale_reads.get(key)
                if stale is not None and int(stale[0]) > 0:
                    stale[0] = int(stale[0]) - 1
                    return stale[1] if isinstance(stale[1], bytes) else None
                return store.values.get(key)

            def publish(self, *, content: bytes) -> dict[str, object]:
                payload = bytes(content)
                store.writes.append((key, payload))
                if key in store.definite_fail_once:
                    store.definite_fail_once.remove(key)
                    raise PublicationError("simulated definite write rejection")
                previous = store.values.get(key)
                store.values[key] = payload
                if key in store.visible_ambiguous_read_error_once:
                    store.visible_ambiguous_read_error_once.remove(key)
                    store.read_errors[key] = 1
                    raise CommitUnknownError("simulated ambiguous write then read error")
                if key in store.visible_ambiguous_once:
                    store.visible_ambiguous_once.remove(key)
                    raise CommitUnknownError("simulated committed write with visible readback")
                if key in store.ambiguous_once:
                    store.ambiguous_once.remove(key)
                    store.stale_reads[key] = [10, previous]
                    raise CommitUnknownError("simulated committed write with lost response")
                if key in store.stale_success_once:
                    store.stale_success_once.remove(key)
                    store.stale_reads[key] = [10, previous]
                return {
                    "key": key,
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }

        return Publisher()


def _single_readback(monkeypatch) -> None:
    monkeypatch.setattr("clash_relay.release_bundle._READ_BACK_DELAYS", (0.0,))


def test_first_release_ambiguous_activation_stops_without_pointer_compensation(
    monkeypatch,
) -> None:
    _single_readback(monkeypatch)
    kv = AmbiguousKV()
    key = "production-config"
    content = b"candidate\n"
    keys = release_keys(key)
    kv.ambiguous_once.add(key)

    with pytest.raises(CommitUnknownError) as captured:
        publish_release_bundle(factory=kv.factory, production_key=key, content=content)

    assert captured.value.production_changed == "unknown"
    assert kv.values[key] == content
    assert parse_release_pointer(kv.values[keys.current_pointer]) == release_id_for(content)
    assert kv.writes.count((key, content)) == 1
    assert (keys.current_pointer, b"\n") not in kv.writes


def test_update_ambiguous_production_write_never_runs_automatic_compensation(monkeypatch) -> None:
    _single_readback(monkeypatch)
    kv = AmbiguousKV()
    key = "production-config"
    first = b"first\n"
    second = b"second\n"
    publish_release_bundle(factory=kv.factory, production_key=key, content=first)
    keys = release_keys(key)
    first_id = release_id_for(first)
    kv.ambiguous_once.add(key)

    with pytest.raises(CommitUnknownError) as captured:
        publish_release_bundle(factory=kv.factory, production_key=key, content=second)

    assert captured.value.production_changed == "unknown"
    assert kv.values[key] == second
    assert parse_release_pointer(kv.values[keys.current_pointer]) == first_id
    assert keys.previous_pointer not in kv.values
    assert kv.writes.count((key, first)) == 1
    assert kv.writes.count((key, second)) == 1


def test_definite_update_rejection_runs_verified_compensation(monkeypatch) -> None:
    _single_readback(monkeypatch)
    kv = AmbiguousKV()
    key = "production-config"
    first = b"first\n"
    second = b"second\n"
    publish_release_bundle(factory=kv.factory, production_key=key, content=first)
    keys = release_keys(key)
    first_id = release_id_for(first)
    kv.definite_fail_once.add(key)

    with pytest.raises(PublicationError, match="previous production bytes were restored"):
        publish_release_bundle(factory=kv.factory, production_key=key, content=second)

    assert kv.values[key] == first
    assert parse_release_pointer(kv.values[keys.current_pointer]) == first_id
    assert parse_release_pointer(kv.values[keys.previous_pointer]) == first_id
    assert kv.writes.count((key, second)) == 1
    assert kv.writes.count((key, first)) == 2


def test_pointer_commit_unknown_after_verified_activation_reports_production_changed_true(
    monkeypatch,
) -> None:
    _single_readback(monkeypatch)
    kv = AmbiguousKV()
    key = "production-config"
    first = b"first\n"
    second = b"second\n"
    publish_release_bundle(factory=kv.factory, production_key=key, content=first)
    keys = release_keys(key)
    first_id = release_id_for(first)
    kv.ambiguous_once.add(keys.previous_pointer)

    with pytest.raises(CommitUnknownError) as captured:
        publish_release_bundle(factory=kv.factory, production_key=key, content=second)

    assert captured.value.production_changed is True
    assert kv.values[key] == second
    assert parse_release_pointer(kv.values[keys.previous_pointer]) == first_id
    assert parse_release_pointer(kv.values[keys.current_pointer]) == first_id
    second_pointer = f"{release_id_for(second)}\n".encode("ascii")
    assert (keys.current_pointer, second_pointer) not in kv.writes


def test_typed_ambiguous_write_recovers_when_exact_readback_is_visible(monkeypatch) -> None:
    _single_readback(monkeypatch)
    kv = AmbiguousKV()
    key = "production-config"
    content = b"candidate\n"
    kv.visible_ambiguous_once.add(key)

    result = publish_release_bundle(factory=kv.factory, production_key=key, content=content)

    assert result["status"] == "published"
    assert result["production_changed"] is True
    assert kv.values[key] == content


def test_ambiguous_write_can_recover_after_transient_read_error(monkeypatch) -> None:
    monkeypatch.setattr("clash_relay.release_bundle._READ_BACK_DELAYS", (0.0, 0.0))
    kv = AmbiguousKV()
    key = "production-config"
    content = b"candidate\n"
    kv.visible_ambiguous_read_error_once.add(key)

    result = publish_release_bundle(factory=kv.factory, production_key=key, content=content)

    assert result["status"] == "published"
    assert kv.values[key] == content


def test_success_response_with_unconfirmed_readback_becomes_commit_unknown(monkeypatch) -> None:
    _single_readback(monkeypatch)
    kv = AmbiguousKV()
    key = "production-config"
    content = b"candidate\n"
    kv.stale_success_once.add(key)

    with pytest.raises(CommitUnknownError) as captured:
        publish_release_bundle(factory=kv.factory, production_key=key, content=content)

    assert captured.value.production_changed == "unknown"
    assert kv.values[key] == content


def test_immutable_staging_unknown_never_claims_production_changed(monkeypatch) -> None:
    _single_readback(monkeypatch)
    kv = AmbiguousKV()
    key = "production-config"
    content = b"candidate\n"
    keys = release_keys(key)
    kv.ambiguous_once.add(keys.config(release_id_for(content)))

    with pytest.raises(CommitUnknownError) as captured:
        publish_release_bundle(factory=kv.factory, production_key=key, content=content)

    assert captured.value.production_changed is False
    assert key not in kv.values


def test_first_release_pointer_unknown_stops_before_production_write(monkeypatch) -> None:
    _single_readback(monkeypatch)
    kv = AmbiguousKV()
    key = "production-config"
    content = b"candidate\n"
    keys = release_keys(key)
    kv.ambiguous_once.add(keys.current_pointer)

    with pytest.raises(CommitUnknownError) as captured:
        publish_release_bundle(factory=kv.factory, production_key=key, content=content)

    assert captured.value.production_changed is False
    assert key not in kv.values


def test_unchanged_release_pointer_repair_unknown_is_non_production_change(monkeypatch) -> None:
    _single_readback(monkeypatch)
    kv = AmbiguousKV()
    key = "production-config"
    content = b"candidate\n"
    publish_release_bundle(factory=kv.factory, production_key=key, content=content)
    keys = release_keys(key)
    kv.values.pop(keys.current_pointer)
    kv.ambiguous_once.add(keys.current_pointer)

    with pytest.raises(CommitUnknownError) as captured:
        publish_release_bundle(factory=kv.factory, production_key=key, content=content)

    assert captured.value.production_changed is False
    assert kv.values[key] == content


def test_current_pointer_unknown_after_update_preserves_changed_production_evidence(
    monkeypatch,
) -> None:
    _single_readback(monkeypatch)
    kv = AmbiguousKV()
    key = "production-config"
    first = b"first\n"
    second = b"second\n"
    publish_release_bundle(factory=kv.factory, production_key=key, content=first)
    keys = release_keys(key)
    kv.ambiguous_once.add(keys.current_pointer)

    with pytest.raises(CommitUnknownError) as captured:
        publish_release_bundle(factory=kv.factory, production_key=key, content=second)

    assert captured.value.production_changed is True
    assert kv.values[key] == second
    assert parse_release_pointer(kv.values[keys.current_pointer]) == release_id_for(second)


def test_commit_unknown_diagnostic_is_distinct_and_preserves_change_evidence() -> None:
    error = CommitUnknownError(
        "details must never enter diagnostics",
        production_changed="unknown",
    )

    assert safe_failure_diagnostic(error) == {
        "status": "failed",
        "category": "cloudflare_commit_unknown",
        "production_changed": "unknown",
    }
