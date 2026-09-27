from __future__ import annotations

from clash_relay.release_bundle import release_id_for, release_keys
from clash_relay.release_inventory import plan_release_inventory
from clash_relay.release_journal import (
    empty_release_journal,
    plan_release_retention,
    record_release_observation,
)


def test_inventory_identifies_unjournaled_pairs_without_proposing_deletion() -> None:
    keys = release_keys("production-config")
    live = release_id_for(b"live")
    orphan = release_id_for(b"staged")
    incomplete = release_id_for(b"partial")
    result = plan_release_inventory(
        production_key=keys.production,
        names=[
            keys.config(live),
            keys.manifest(live),
            keys.config(orphan),
            keys.manifest(orphan),
            keys.config(incomplete),
        ],
        journal_ids={live},
        current_release_id=None,
        previous_release_id=None,
        production_release_id=live,
    )
    assert result["unrecorded_unprotected_release_ids"] == sorted((orphan, incomplete))
    assert result["incomplete_release_ids"] == [incomplete]
    assert result["protected_release_ids"] == [live]
    assert result["mutation"] == "none"


def test_retention_protects_client_visible_bytes_when_pointer_has_drifted() -> None:
    live = release_id_for(b"live")
    journal = record_release_observation(empty_release_journal(), release_id=live, now_epoch=1)
    plan = plan_release_retention(
        journal,
        current_release_id=None,
        previous_release_id=None,
        production_release_id=live,
        retain_seconds=1,
        now_epoch=100,
    )
    assert plan.deletion_candidate_ids == ()
    assert plan.protected_release_ids == (live,)
