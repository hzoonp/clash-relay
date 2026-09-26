from __future__ import annotations

import pytest

from clash_relay.errors import CommitUnknownError, PublicationError
from clash_relay.release_bundle import parse_release_pointer, publish_release_bundle, release_keys


class Store:
    def __init__(self):
        self.values = {}
        self.delete_fails = False

    def factory(self, key):
        store = self

        class Value:
            def read(self):
                return store.values.get(key)

            def publish(self, *, content):
                store.values[key] = content
                return {}

            def delete(self):
                if store.delete_fails:
                    raise CommitUnknownError("unconfirmed deletion")
                store.values.pop(key, None)
                return {}

        return Value()

    def publish(self, content, verify=None):
        return publish_release_bundle(
            factory=self.factory, production_key="production", content=content, verify_active=verify
        )


def _failed_smoke():
    raise PublicationError("final-link smoke failed")


def test_smoke_runs_after_activation_before_pointer_commit():
    store = Store()
    first = store.publish(b"first")
    keys = release_keys("production")

    def smoke():
        assert store.values["production"] == b"second"
        assert parse_release_pointer(store.values[keys.current_pointer]) == first["release_id"]
        return {"status": "passed"}

    result = store.publish(b"second", smoke)
    assert result["final_link_smoke"] == {"status": "passed"}
    assert parse_release_pointer(store.values[keys.current_pointer]) == result["release_id"]


def test_second_release_smoke_failure_preserves_empty_previous_pointer():
    store = Store()
    first = store.publish(b"first")
    keys = release_keys("production")
    assert parse_release_pointer(store.values.get(keys.previous_pointer)) is None

    with pytest.raises(PublicationError, match="previous production bytes were restored"):
        store.publish(b"second", _failed_smoke)

    assert store.values["production"] == b"first"
    assert parse_release_pointer(store.values[keys.current_pointer]) == first["release_id"]
    assert parse_release_pointer(store.values.get(keys.previous_pointer)) is None


def test_failed_smoke_restores_previous_production_and_history():
    store = Store()
    store.publish(b"first")
    second = store.publish(b"second")
    keys = release_keys("production")
    previous_before = store.values[keys.previous_pointer]
    with pytest.raises(PublicationError, match="previous production bytes were restored"):
        store.publish(b"third", _failed_smoke)
    assert store.values["production"] == b"second"
    assert parse_release_pointer(store.values[keys.current_pointer]) == second["release_id"]
    assert store.values[keys.previous_pointer] == previous_before


def test_first_release_smoke_failure_removes_activation():
    store = Store()
    with pytest.raises(PublicationError, match="new production value was removed"):
        store.publish(b"first", _failed_smoke)
    assert "production" not in store.values
    assert parse_release_pointer(store.values[release_keys("production").current_pointer]) is None


def test_unknown_bootstrap_cleanup_is_reported_honestly():
    store = Store()
    store.delete_fails = True
    with pytest.raises(CommitUnknownError, match="cleanup state is unknown") as caught:
        store.publish(b"first", _failed_smoke)
    assert caught.value.production_changed == "unknown"


def test_unchanged_release_still_checks_entry_and_does_not_rotate_history():
    store = Store()
    store.publish(b"first")
    store.publish(b"second")
    before = dict(store.values)
    with pytest.raises(PublicationError, match="smoke"):
        store.publish(b"second", _failed_smoke)
    assert store.values == before
