"""Private release intent retained across interrupted KV pointer updates."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .errors import PublicationError

_CURRENT_VERSION = 2
_LEGACY_VERSION = 1
_LEGACY_PHASE = "prepared"
_PHASES = ("prepare", "commit", "verify", "finalize")
_PHASE_INDEX = {phase: index for index, phase in enumerate(_PHASES)}


@dataclass(frozen=True, slots=True)
class ReleaseTransaction:
    from_release_id: str | None
    to_release_id: str
    previous_release_id: str | None
    phase: str = "prepare"

    @property
    def referenced_release_ids(self) -> set[str]:
        return {
            value
            for value in (self.from_release_id, self.to_release_id, self.previous_release_id)
            if value is not None
        }


def _validate_phase(phase: str) -> None:
    if phase not in _PHASE_INDEX:
        raise PublicationError("release transaction phase is invalid")


def advance_release_transaction(
    transaction: ReleaseTransaction,
    phase: str,
) -> ReleaseTransaction:
    """Advance a transaction monotonically without changing its release identities."""
    _validate_phase(transaction.phase)
    _validate_phase(phase)
    if _PHASE_INDEX[phase] < _PHASE_INDEX[transaction.phase]:
        raise PublicationError("release transaction phase regression is invalid")
    if phase == transaction.phase:
        return transaction
    return ReleaseTransaction(
        from_release_id=transaction.from_release_id,
        to_release_id=transaction.to_release_id,
        previous_release_id=transaction.previous_release_id,
        phase=phase,
    )


def parse_release_transaction(content: bytes | None) -> ReleaseTransaction | None:
    if content is None or content == b"\n":
        return None
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PublicationError("release transaction is invalid") from exc
    if (
        not isinstance(value, dict)
        or set(value)
        != {"version", "phase", "from_release_id", "to_release_id", "previous_release_id"}
        or type(value["version"]) is not int
        or not isinstance(value["phase"], str)
    ):
        raise PublicationError("release transaction is invalid")

    version = value["version"]
    raw_phase = value["phase"]
    if version == _LEGACY_VERSION and raw_phase == _LEGACY_PHASE:
        phase = "prepare"
    elif version == _CURRENT_VERSION and raw_phase in _PHASE_INDEX:
        phase = raw_phase
    else:
        raise PublicationError("release transaction is invalid")

    from .release_bundle import _validate_release_id

    for field in ("from_release_id", "to_release_id", "previous_release_id"):
        item = value[field]
        if item is None and field != "to_release_id":
            continue
        if not isinstance(item, str):
            raise PublicationError("release transaction is invalid")
        _validate_release_id(item)
    if value["from_release_id"] == value["to_release_id"]:
        raise PublicationError("release transaction has identical endpoints")
    if value["from_release_id"] is None and value["previous_release_id"] is not None:
        raise PublicationError("first release transaction has a previous release")
    return ReleaseTransaction(
        from_release_id=value["from_release_id"],
        to_release_id=value["to_release_id"],
        previous_release_id=value["previous_release_id"],
        phase=phase,
    )


def serialize_release_transaction(transaction: ReleaseTransaction) -> bytes:
    _validate_phase(transaction.phase)
    value = {
        "version": _CURRENT_VERSION,
        "phase": transaction.phase,
        "from_release_id": transaction.from_release_id,
        "to_release_id": transaction.to_release_id,
        "previous_release_id": transaction.previous_release_id,
    }
    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    parse_release_transaction(encoded)
    return encoded


def inspect_release_state(
    *,
    production_release_id: str | None,
    current_release_id: str | None,
    previous_release_id: str | None,
    transaction: ReleaseTransaction | None,
) -> str:
    """Classify observable IDs without assuming KV reads are strongly consistent."""
    if transaction is None:
        if (
            production_release_id is None
            and current_release_id is None
            and previous_release_id is None
        ):
            return "first_release"
        if production_release_id == current_release_id and production_release_id is not None:
            return "inconsistent" if previous_release_id == current_release_id else "healthy"
        return "inconsistent"
    if production_release_id == transaction.to_release_id:
        if current_release_id in {
            None,
            transaction.from_release_id,
            transaction.to_release_id,
        } and previous_release_id in {transaction.previous_release_id, transaction.from_release_id}:
            return "recoverable_interrupted_commit"
        return "inconsistent"
    if production_release_id == transaction.from_release_id:
        return "ambiguous"
    return "inconsistent"
