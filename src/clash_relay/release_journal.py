"""Private, append-only release observation journal and retention planning."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from .errors import PublicationError

_VERSION = 1
_MAX_RECORDS = 4096
_WARNING_RECORDS = int(_MAX_RECORDS * 0.875)


@dataclass(frozen=True, slots=True)
class ReleaseRetentionPlan:
    retention_seconds: int
    protected_release_ids: tuple[str, ...]
    deletion_candidate_ids: tuple[str, ...]
    retained_by_window: int
    invalid_records: int

    @property
    def plan_id(self) -> str:
        payload = {
            "retention_seconds": self.retention_seconds,
            "protected_release_ids": self.protected_release_ids,
            "deletion_candidate_ids": self.deletion_candidate_ids,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "planned",
            "plan_id": self.plan_id,
            "retention_seconds": self.retention_seconds,
            "protected_release_ids": list(self.protected_release_ids),
            "deletion_candidate_ids": list(self.deletion_candidate_ids),
            "retained_by_window": self.retained_by_window,
            "invalid_records": self.invalid_records,
            "mutation": "none",
        }


def empty_release_journal() -> dict[str, Any]:
    return {"version": _VERSION, "releases": {}}


def release_journal_summary(journal: dict[str, Any]) -> dict[str, object]:
    parsed, status = parse_release_journal(_encode(journal))
    if status == "invalid":
        raise PublicationError("release journal is invalid")
    records = len(parsed["releases"])
    return {
        "records": records,
        "capacity": _MAX_RECORDS,
        "capacity_status": "warning" if records >= _WARNING_RECORDS else "healthy",
    }


def parse_release_journal(content: bytes | None) -> tuple[dict[str, Any], str]:
    if not content:
        return empty_release_journal(), "missing"
    try:
        import json

        document = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return empty_release_journal(), "invalid"
    if (
        not isinstance(document, dict)
        or document.get("version") != _VERSION
        or not isinstance(document.get("releases"), dict)
    ):
        return empty_release_journal(), "invalid"
    releases = document["releases"]
    clean: dict[str, dict[str, int]] = {}
    from .release_bundle import _validate_release_id

    for release_id, record in releases.items():
        if not isinstance(release_id, str) or not isinstance(record, dict):
            return empty_release_journal(), "invalid"
        try:
            _validate_release_id(release_id)
        except PublicationError:
            return empty_release_journal(), "invalid"
        first_seen = record.get("first_seen_epoch")
        if not isinstance(first_seen, int) or first_seen < 0:
            return empty_release_journal(), "invalid"
        clean[release_id] = {"first_seen_epoch": first_seen}
    if len(clean) > _MAX_RECORDS:
        return empty_release_journal(), "invalid"
    return {"version": _VERSION, "releases": clean}, "loaded"


def record_release_observation(
    journal: dict[str, Any], *, release_id: str, now_epoch: int | None = None
) -> dict[str, Any]:
    from .release_bundle import _validate_release_id

    _validate_release_id(release_id)
    parsed, status = parse_release_journal(_encode(journal))
    if status == "invalid":
        raise PublicationError("release journal is invalid")
    releases = dict(parsed["releases"])
    if release_id not in releases:
        if len(releases) >= _MAX_RECORDS:
            raise PublicationError("release journal record limit reached")
        releases[release_id] = {
            "first_seen_epoch": int(time.time() if now_epoch is None else now_epoch)
        }
    return {"version": _VERSION, "releases": releases}


def plan_release_retention(
    journal: dict[str, Any],
    *,
    current_release_id: str | None,
    previous_release_id: str | None,
    retain_seconds: int,
    now_epoch: int | None = None,
) -> ReleaseRetentionPlan:
    if retain_seconds < 0:
        raise PublicationError("release retention duration must not be negative")
    parsed, status = parse_release_journal(_encode(journal))
    if status == "invalid":
        raise PublicationError("release journal is invalid")
    from .release_bundle import _validate_release_id

    protected = {item for item in (current_release_id, previous_release_id) if item is not None}
    for release_id in protected:
        _validate_release_id(release_id)
    cutoff = int(time.time() if now_epoch is None else now_epoch) - retain_seconds
    retained_by_window = 0
    candidates: list[str] = []
    for release_id, record in parsed["releases"].items():
        first_seen = record["first_seen_epoch"]
        if release_id in protected:
            continue
        if first_seen >= cutoff:
            retained_by_window += 1
            continue
        candidates.append(release_id)
    return ReleaseRetentionPlan(
        retention_seconds=retain_seconds,
        protected_release_ids=tuple(sorted(protected)),
        deletion_candidate_ids=tuple(sorted(candidates)),
        retained_by_window=retained_by_window,
        invalid_records=0,
    )


def remove_release_observations(journal: dict[str, Any], release_ids: set[str]) -> dict[str, Any]:
    parsed, status = parse_release_journal(_encode(journal))
    if status == "invalid":
        raise PublicationError("release journal is invalid")
    releases = {
        release_id: record
        for release_id, record in parsed["releases"].items()
        if release_id not in release_ids
    }
    return {"version": _VERSION, "releases": releases}


def serialize_release_journal(journal: dict[str, Any]) -> bytes:
    parsed, status = parse_release_journal(_encode(journal))
    if status == "invalid":
        raise PublicationError("release journal is invalid")
    return _encode(parsed)


def _encode(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
