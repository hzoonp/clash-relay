"""Fail-closed publication latch for the canonical production entrypoint."""

from __future__ import annotations

from .production_lifecycle import resolve_publication_mode


def resolve_cutover_publication_mode(
    *,
    explicit_publish: bool | None = None,
    event_name: str | None = None,
    manual_publish: str | bool | None = None,
) -> bool:
    """Keep automatic GitHub events dry-run until publication is explicit."""

    if event_name in {"push", "schedule"}:
        return False
    return resolve_publication_mode(
        explicit_publish=explicit_publish,
        event_name=event_name,
        manual_publish=manual_publish,
    )
