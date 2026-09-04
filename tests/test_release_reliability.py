from __future__ import annotations

import pytest

from clash_relay.errors import ValidationError
from clash_relay.release_reliability import ReleasePhase, ReleaseProgress


def test_published_release_requires_all_phases_in_order() -> None:
    progress = ReleaseProgress(publish=True)
    for phase in (
        ReleasePhase.PREPARED,
        ReleasePhase.QUALIFIED,
        ReleasePhase.PROMOTED,
        ReleasePhase.PUBLISHED,
        ReleasePhase.VERIFIED,
    ):
        progress.advance(phase)

    assert progress.phase == "verified"
    assert progress.history == (
        "prepared",
        "qualified",
        "promoted",
        "published",
        "verified",
    )


def test_dry_run_skips_published_phase_but_still_verifies_candidate() -> None:
    progress = ReleaseProgress(publish=False)
    for phase in (
        ReleasePhase.PREPARED,
        ReleasePhase.QUALIFIED,
        ReleasePhase.PROMOTED,
        ReleasePhase.VERIFIED,
    ):
        progress.advance(phase)

    assert progress.safe_summary() == {
        "phase": "verified",
        "history": ["prepared", "qualified", "promoted", "verified"],
        "publication_requested": False,
    }


def test_release_phase_cannot_skip_promotion_or_repeat_phase() -> None:
    progress = ReleaseProgress(publish=True)
    progress.advance(ReleasePhase.PREPARED)

    with pytest.raises(ValidationError, match="expected 'qualified'"):
        progress.advance(ReleasePhase.PUBLISHED)

    progress.advance(ReleasePhase.QUALIFIED)
    with pytest.raises(ValidationError, match="cannot be entered twice"):
        progress.advance(ReleasePhase.QUALIFIED)
