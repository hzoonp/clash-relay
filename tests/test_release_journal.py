from __future__ import annotations

import pytest

from clash_relay.errors import PublicationError
from clash_relay.release_bundle import release_id_for
from clash_relay.release_journal import (
    empty_release_journal,
    parse_release_journal,
    plan_release_retention,
    record_release_observation,
    release_journal_summary,
    remove_release_observations,
    serialize_release_journal,
)


def test_release_journal_round_trips_deterministically() -> None:
    release_id = release_id_for(b"release\n")
    journal = record_release_observation(
        empty_release_journal(), release_id=release_id, now_epoch=100
    )

    parsed, status = parse_release_journal(serialize_release_journal(journal))

    assert status == "loaded"
    assert parsed == {"version": 1, "releases": {release_id: {"first_seen_epoch": 100}}}


def test_release_journal_keeps_first_observation() -> None:
    release_id = release_id_for(b"release\n")
    initial = record_release_observation(
        empty_release_journal(), release_id=release_id, now_epoch=100
    )

    assert record_release_observation(initial, release_id=release_id, now_epoch=200) == initial


def test_retention_plan_always_protects_current_and_previous() -> None:
    oldest = release_id_for(b"oldest\n")
    previous = release_id_for(b"previous\n")
    current = release_id_for(b"current\n")
    recent = release_id_for(b"recent\n")
    journal = {"version": 1, "releases": {}}
    for release_id, seen in ((oldest, 10), (previous, 10), (current, 10), (recent, 90)):
        journal = record_release_observation(journal, release_id=release_id, now_epoch=seen)

    plan = plan_release_retention(
        journal,
        current_release_id=current,
        previous_release_id=previous,
        retain_seconds=30,
        now_epoch=100,
    )

    assert plan.protected_release_ids == tuple(sorted((current, previous)))
    assert plan.deletion_candidate_ids == (oldest,)
    assert plan.retained_by_window == 1
    assert plan.to_dict()["mutation"] == "none"


def test_invalid_journal_never_produces_a_deletion_plan() -> None:
    _invalid, status = parse_release_journal(b'{"version":1,"releases":{"bad":{}}}')

    assert status == "invalid"
    with pytest.raises(PublicationError, match="invalid"):
        plan_release_retention(
            {"version": 1, "releases": {"bad": {}}},
            current_release_id=None,
            previous_release_id=None,
            retain_seconds=1,
        )


def test_remove_observations_keeps_unrelated_release_records() -> None:
    first = release_id_for(b"first\n")
    second = release_id_for(b"second\n")
    journal = record_release_observation(empty_release_journal(), release_id=first, now_epoch=1)
    journal = record_release_observation(journal, release_id=second, now_epoch=2)

    assert remove_release_observations(journal, {first}) == {
        "version": 1,
        "releases": {second: {"first_seen_epoch": 2}},
    }


def test_release_journal_summary_reports_capacity_warning_before_hard_limit() -> None:
    journal = empty_release_journal()
    journal["releases"] = {f"{index:064x}": {"first_seen_epoch": index} for index in range(3584)}

    assert release_journal_summary(journal) == {
        "records": 3584,
        "capacity": 4096,
        "capacity_status": "warning",
    }
