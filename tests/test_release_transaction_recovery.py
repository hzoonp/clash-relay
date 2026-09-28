"""Durable release intent protects rollback history across process restarts."""

from __future__ import annotations

import hashlib

import pytest

from clash_relay.errors import CommitUnknownError, PublicationError
from clash_relay.release_bundle import (
    parse_release_pointer,
    publish_release_bundle,
    read_previous_release,
    release_id_for,
    release_keys,
)
from clash_relay.release_inventory import plan_release_inventory
from clash_relay.release_journal import plan_release_retention, record_release_observation
from clash_relay.release_reconciliation import ReconciliationStatus, reconcile_release_bundle
from clash_relay.release_transaction import (
    ReleaseTransaction,
    inspect_release_state,
    parse_release_transaction,
    serialize_release_transaction,
)


class MemoryKV:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def factory(self, key: str):
        store = self

        class Publisher:
            def read(self) -> bytes | None:
                return store.values.get(key)

            def publish(self, *, content: bytes) -> dict[str, object]:
                store.values[key] = bytes(content)
                return {
                    "key": key,
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }

        return Publisher()


def _interrupted_update() -> tuple[MemoryKV, str, bytes, bytes, bytes]:
    store = MemoryKV()
    key = "production-config"
    older, before, candidate = b"older\n", b"before\n", b"candidate\n"
    publish_release_bundle(factory=store.factory, production_key=key, content=older)
    publish_release_bundle(factory=store.factory, production_key=key, content=before)
    keys = release_keys(key)
    store.values[keys.production] = candidate
    store.values[keys.transaction] = serialize_release_transaction(
        ReleaseTransaction(release_id_for(before), release_id_for(candidate), release_id_for(older))
    )
    return store, key, older, before, candidate


def test_retry_recovers_immediate_rollback_predecessor() -> None:
    store, key, older, before, candidate = _interrupted_update()
    keys = release_keys(key)
    assert parse_release_pointer(store.values[keys.previous_pointer]) == release_id_for(older)

    result = publish_release_bundle(factory=store.factory, production_key=key, content=candidate)

    assert result["recovered_transaction"] is True
    assert result["previous_release_id"] == release_id_for(before)
    assert parse_release_pointer(store.values[keys.current_pointer]) == release_id_for(candidate)
    assert parse_release_pointer(store.values[keys.previous_pointer]) == release_id_for(before)
    assert parse_release_transaction(store.values[keys.transaction]) is None
    assert read_previous_release(factory=store.factory, production_key=key)[0] == before


@pytest.mark.parametrize("crash_key", ["production", "previous_pointer", "current_pointer"])
def test_restart_after_each_mutating_commit_repairs_history(crash_key: str) -> None:
    store = MemoryKV()
    key = "production-config"
    keys = release_keys(key)
    older, before, candidate = b"older\n", b"before\n", b"candidate\n"
    publish_release_bundle(factory=store.factory, production_key=key, content=older)
    publish_release_bundle(factory=store.factory, production_key=key, content=before)
    target = getattr(keys, crash_key)
    underlying = store.factory
    triggered = False

    def crashing_factory(name: str):
        publisher = underlying(name)

        class CrashPublisher:
            def read(self) -> bytes | None:
                return publisher.read()

            def publish(self, *, content: bytes) -> dict[str, object]:
                nonlocal triggered
                result = publisher.publish(content=content)
                if name == target and not triggered:
                    triggered = True
                    raise SystemExit("process exited after the KV commit")
                return result

        return CrashPublisher()

    with pytest.raises(SystemExit):
        publish_release_bundle(factory=crashing_factory, production_key=key, content=candidate)
    assert triggered
    recovered = publish_release_bundle(factory=store.factory, production_key=key, content=candidate)
    assert recovered["recovered_transaction"] is True
    assert read_previous_release(factory=store.factory, production_key=key)[0] == before


def test_other_candidate_and_rollback_stop_during_pending_transaction() -> None:
    store, key, _, _, candidate = _interrupted_update()
    alternate = b"alternate\n"
    with pytest.raises(PublicationError, match="another candidate"):
        publish_release_bundle(factory=store.factory, production_key=key, content=alternate)
    assert release_keys(key).config(release_id_for(alternate)) not in store.values
    with pytest.raises(PublicationError, match="unfinished transaction"):
        read_previous_release(factory=store.factory, production_key=key)
    assert store.values[key] == candidate


def test_pending_transaction_rejects_contradictory_or_missing_predecessor() -> None:
    store, key, older, before, candidate = _interrupted_update()
    keys = release_keys(key)
    store.values[keys.current_pointer] = f"{release_id_for(older)}\n".encode()
    with pytest.raises(PublicationError, match="contradict"):
        publish_release_bundle(factory=store.factory, production_key=key, content=candidate)

    store.values[keys.current_pointer] = f"{release_id_for(before)}\n".encode()
    store.values.pop(keys.config(release_id_for(before)))
    with pytest.raises(PublicationError, match="predecessor immutable"):
        publish_release_bundle(factory=store.factory, production_key=key, content=candidate)


def test_release_without_intent_rejects_inconsistent_pointer_state() -> None:
    store = MemoryKV()
    keys = release_keys("production-config")
    old = b"old\n"
    store.values[keys.current_pointer] = f"{release_id_for(old)}\n".encode()
    with pytest.raises(PublicationError, match="without production"):
        publish_release_bundle(
            factory=store.factory, production_key=keys.production, content=b"new\n"
        )

    store.values.pop(keys.current_pointer)
    store.values[keys.previous_pointer] = f"{release_id_for(old)}\n".encode()
    with pytest.raises(PublicationError, match="without production"):
        publish_release_bundle(
            factory=store.factory, production_key=keys.production, content=b"new\n"
        )

    store.values[keys.production] = old
    store.values[keys.current_pointer] = f"{release_id_for(b'unrelated')}\n".encode()
    with pytest.raises(PublicationError, match="disagree without transaction"):
        publish_release_bundle(
            factory=store.factory, production_key=keys.production, content=b"new\n"
        )


def test_release_observer_captures_the_actual_pre_attempt_bytes() -> None:
    store = MemoryKV()
    key = "production-config"
    publish_release_bundle(factory=store.factory, production_key=key, content=b"old\n")
    captured: list[bytes | None] = []

    publish_release_bundle(
        factory=store.factory,
        production_key=key,
        content=b"new\n",
        baseline_observer=captured.append,
    )
    assert captured == [b"old\n"]


def test_ambiguous_transaction_intent_write_stops_before_activation() -> None:
    store = MemoryKV()
    key = "production-config"
    keys = release_keys(key)
    publish_release_bundle(factory=store.factory, production_key=key, content=b"old\n")
    underlying = store.factory

    def ambiguous_factory(name: str):
        publisher = underlying(name)

        class Value:
            def read(self) -> bytes | None:
                return None if name == keys.transaction else publisher.read()

            def publish(self, *, content: bytes):
                if name == keys.transaction:
                    raise CommitUnknownError("injected transaction response loss")
                return publisher.publish(content=content)

        return Value()

    with pytest.raises(CommitUnknownError, match="transaction state is unknown"):
        publish_release_bundle(factory=ambiguous_factory, production_key=key, content=b"new\n")
    assert store.values[keys.production] == b"old\n"


def test_interrupted_legacy_pointer_can_recover_from_verified_intent() -> None:
    store, key, _, before, candidate = _interrupted_update()
    keys = release_keys(key)
    store.values.pop(keys.current_pointer)

    result = publish_release_bundle(factory=store.factory, production_key=key, content=candidate)

    assert result["recovered_transaction"] is True
    assert parse_release_pointer(store.values[keys.current_pointer]) == release_id_for(candidate)
    assert read_previous_release(factory=store.factory, production_key=key)[0] == before


def test_old_production_observation_keeps_transaction_ambiguous() -> None:
    store, key, _, before, candidate = _interrupted_update()
    store.values[key] = before
    with pytest.raises(CommitUnknownError):
        publish_release_bundle(factory=store.factory, production_key=key, content=candidate)
    reconciliation = reconcile_release_bundle(
        factory=store.factory,
        production_key=key,
        candidate_content=candidate,
        previous_content=before,
    )
    assert reconciliation.status is ReconciliationStatus.UNKNOWN
    assert reconciliation.reason == "transaction_incomplete_or_not_yet_visible"


def test_transaction_refs_are_retained_even_outside_window() -> None:
    store, key, older, before, candidate = _interrupted_update()
    transaction = parse_release_transaction(store.values[release_keys(key).transaction])
    assert transaction is not None
    journal = {"version": 1, "releases": {}}
    for content in (older, before, candidate):
        journal = record_release_observation(
            journal, release_id=release_id_for(content), now_epoch=1
        )
    plan = plan_release_retention(
        journal,
        current_release_id=release_id_for(before),
        previous_release_id=release_id_for(older),
        production_release_id=release_id_for(candidate),
        transaction_release_ids=transaction.referenced_release_ids,
        retain_seconds=1,
        now_epoch=100,
    )
    assert plan.deletion_candidate_ids == ()
    inventory = plan_release_inventory(
        production_key=key,
        names=[release_keys(key).config(release_id_for(before))],
        journal_ids=set(),
        current_release_id=None,
        previous_release_id=None,
        production_release_id=None,
        transaction_release_ids=transaction.referenced_release_ids,
    )
    assert release_id_for(before) not in inventory["unrecorded_unprotected_release_ids"]
    assert (
        inspect_release_state(
            production_release_id=release_id_for(candidate),
            current_release_id=release_id_for(before),
            previous_release_id=release_id_for(older),
            transaction=transaction,
        )
        == "recoverable_interrupted_commit"
    )


@pytest.mark.parametrize("raw", [b"{}", b"not-json", b"\xff", b"{}\n"])
def test_invalid_transaction_fails_closed(raw: bytes) -> None:
    with pytest.raises(PublicationError):
        parse_release_transaction(raw)
