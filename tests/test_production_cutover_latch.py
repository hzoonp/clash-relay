from __future__ import annotations

from clash_relay.production_cutover import resolve_cutover_publication_mode


def test_automatic_github_events_are_always_dry_run() -> None:
    for event_name in ("push", "schedule"):
        assert resolve_cutover_publication_mode(event_name=event_name) is False
        assert (
            resolve_cutover_publication_mode(
                explicit_publish=True,
                event_name=event_name,
                manual_publish=True,
            )
            is False
        )


def test_manual_dispatch_requires_explicit_publication_enablement() -> None:
    assert (
        resolve_cutover_publication_mode(
            event_name="workflow_dispatch",
            manual_publish=False,
        )
        is False
    )
    assert (
        resolve_cutover_publication_mode(
            event_name="workflow_dispatch",
            manual_publish="false",
        )
        is False
    )
    assert (
        resolve_cutover_publication_mode(
            event_name="workflow_dispatch",
            manual_publish=True,
        )
        is True
    )
    assert (
        resolve_cutover_publication_mode(
            event_name="workflow_dispatch",
            manual_publish=" TRUE ",
        )
        is True
    )


def test_local_explicit_publication_remains_available_outside_automatic_events() -> None:
    assert resolve_cutover_publication_mode(explicit_publish=True) is True
    assert resolve_cutover_publication_mode(explicit_publish=False) is False
