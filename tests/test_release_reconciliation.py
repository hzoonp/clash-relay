from __future__ import annotations

import pytest

from clash_relay.errors import PublicationError
from clash_relay.release_bundle import release_id_for, release_keys
from clash_relay.release_reconciliation import ReconciliationStatus, reconcile_release_bundle


class _ReadOnlyValue:
    def __init__(self, state: dict[str, bytes | None], key: str, *, fail_key: str | None) -> None:
        self._state = state
        self._key = key
        self._fail_key = fail_key

    def read(self) -> bytes | None:
        if self._key == self._fail_key:
            raise PublicationError("injected read failure")
        return self._state.get(self._key)

    def publish(self, *, content: bytes):
        raise AssertionError("release reconciliation must never publish")


class _ReadOnlyFactory:
    def __init__(self, state: dict[str, bytes | None], *, fail_key: str | None = None) -> None:
        self.state = state
        self.fail_key = fail_key

    def __call__(self, key: str) -> _ReadOnlyValue:
        return _ReadOnlyValue(self.state, key, fail_key=self.fail_key)


def _pointer(content: bytes) -> bytes:
    return f"{release_id_for(content)}\n".encode("ascii")


def test_reconcile_proves_completed_update_without_writing() -> None:
    production_key = "production-config"
    previous = b"old\n"
    candidate = b"new\n"
    keys = release_keys(production_key)
    factory = _ReadOnlyFactory(
        {
            keys.production: candidate,
            keys.current_pointer: _pointer(candidate),
            keys.previous_pointer: _pointer(previous),
        }
    )

    result = reconcile_release_bundle(
        factory=factory,
        production_key=production_key,
        candidate_content=candidate,
        previous_content=previous,
    )

    assert result.status is ReconciliationStatus.COMMITTED
    assert result.production_changed is True
    assert result.reason == "release_transaction_fully_visible"
    assert result.to_dict()["requires_manual_action"] is False


def test_reconcile_proves_first_release() -> None:
    production_key = "production-config"
    candidate = b"first\n"
    keys = release_keys(production_key)
    result = reconcile_release_bundle(
        factory=_ReadOnlyFactory(
            {
                keys.production: candidate,
                keys.current_pointer: _pointer(candidate),
                keys.previous_pointer: None,
            }
        ),
        production_key=production_key,
        candidate_content=candidate,
        previous_content=None,
    )

    assert result.status is ReconciliationStatus.COMMITTED
    assert result.reason == "first_release_fully_visible"


def test_reconcile_proves_candidate_not_committed() -> None:
    production_key = "production-config"
    previous = b"old\n"
    candidate = b"new\n"
    keys = release_keys(production_key)
    result = reconcile_release_bundle(
        factory=_ReadOnlyFactory(
            {
                keys.production: previous,
                keys.current_pointer: _pointer(previous),
                keys.previous_pointer: None,
            }
        ),
        production_key=production_key,
        candidate_content=candidate,
        previous_content=previous,
    )

    assert result.status is ReconciliationStatus.NOT_COMMITTED
    assert result.production_changed is False
    assert result.to_dict()["requires_manual_action"] is False


def test_reconcile_keeps_candidate_live_with_incomplete_pointers_unknown() -> None:
    production_key = "production-config"
    previous = b"old\n"
    candidate = b"new\n"
    keys = release_keys(production_key)
    result = reconcile_release_bundle(
        factory=_ReadOnlyFactory(
            {
                keys.production: candidate,
                keys.current_pointer: _pointer(candidate),
                keys.previous_pointer: None,
            }
        ),
        production_key=production_key,
        candidate_content=candidate,
        previous_content=previous,
    )

    assert result.status is ReconciliationStatus.UNKNOWN
    assert result.production_changed is True
    assert result.reason == "candidate_live_with_incomplete_pointer_transaction"
    assert result.to_dict()["requires_manual_action"] is True


def test_reconcile_keeps_partial_first_release_unknown() -> None:
    production_key = "production-config"
    candidate = b"first\n"
    keys = release_keys(production_key)
    result = reconcile_release_bundle(
        factory=_ReadOnlyFactory(
            {
                keys.production: None,
                keys.current_pointer: _pointer(candidate),
                keys.previous_pointer: None,
            }
        ),
        production_key=production_key,
        candidate_content=candidate,
        previous_content=None,
    )

    assert result.status is ReconciliationStatus.UNKNOWN
    assert result.production_changed == "unknown"


def test_reconcile_read_failure_remains_unknown() -> None:
    production_key = "production-config"
    candidate = b"new\n"
    keys = release_keys(production_key)
    result = reconcile_release_bundle(
        factory=_ReadOnlyFactory({}, fail_key=keys.production),
        production_key=production_key,
        candidate_content=candidate,
        previous_content=b"old\n",
    )

    assert result.status is ReconciliationStatus.UNKNOWN
    assert result.reason == "read_failed"
    assert result.production_matches_candidate is None


def test_reconcile_rejects_empty_candidate() -> None:
    with pytest.raises(PublicationError, match="empty production release"):
        reconcile_release_bundle(
            factory=_ReadOnlyFactory({}),
            production_key="production-config",
            candidate_content=b"",
            previous_content=None,
        )
