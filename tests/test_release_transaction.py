from __future__ import annotations

import json

import pytest

from clash_relay.errors import PublicationError
from clash_relay.release_bundle import release_id_for
from clash_relay.release_transaction import (
    ReleaseTransaction,
    advance_release_transaction,
    parse_release_transaction,
    serialize_release_transaction,
)


def _rid(value: bytes) -> str:
    return release_id_for(value)


def test_v2_transaction_round_trip_preserves_phase() -> None:
    transaction = ReleaseTransaction(_rid(b"old"), _rid(b"new"), None, phase="commit")

    encoded = serialize_release_transaction(transaction)
    document = json.loads(encoded)

    assert document["version"] == 2
    assert document["phase"] == "commit"
    assert parse_release_transaction(encoded) == transaction


def test_legacy_prepared_transaction_is_read_as_prepare() -> None:
    encoded = (
        json.dumps(
            {
                "version": 1,
                "phase": "prepared",
                "from_release_id": _rid(b"old"),
                "to_release_id": _rid(b"new"),
                "previous_release_id": None,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()

    parsed = parse_release_transaction(encoded)

    assert parsed is not None
    assert parsed.phase == "prepare"
    assert parsed.from_release_id == _rid(b"old")
    assert parsed.to_release_id == _rid(b"new")


@pytest.mark.parametrize("phase", ("prepare", "commit", "verify", "finalize"))
def test_transaction_accepts_all_declared_phases(phase: str) -> None:
    transaction = ReleaseTransaction(None, _rid(b"new"), None, phase=phase)

    assert parse_release_transaction(serialize_release_transaction(transaction)) == transaction


def test_transaction_phase_advancement_is_monotonic() -> None:
    transaction = ReleaseTransaction(None, _rid(b"new"), None)

    commit = advance_release_transaction(transaction, "commit")
    verify = advance_release_transaction(commit, "verify")
    finalize = advance_release_transaction(verify, "finalize")

    assert transaction.phase == "prepare"
    assert commit.phase == "commit"
    assert verify.phase == "verify"
    assert finalize.phase == "finalize"


def test_transaction_phase_regression_is_rejected() -> None:
    transaction = ReleaseTransaction(None, _rid(b"new"), None, phase="verify")

    with pytest.raises(PublicationError, match="phase regression"):
        advance_release_transaction(transaction, "commit")


def test_unknown_transaction_phase_is_rejected() -> None:
    transaction = ReleaseTransaction(None, _rid(b"new"), None, phase="unknown")

    with pytest.raises(PublicationError, match="phase is invalid"):
        serialize_release_transaction(transaction)
