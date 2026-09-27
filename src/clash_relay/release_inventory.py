"""Read-only inventory of immutable KV release objects."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .release_bundle import release_keys

_OBJECT = re.compile(r"^([0-9a-f]{64})\.(config|manifest)$")


def plan_release_inventory(
    *,
    production_key: str,
    names: Iterable[str],
    journal_ids: set[str],
    current_release_id: str | None,
    previous_release_id: str | None,
    production_release_id: str | None,
) -> dict[str, Any]:
    """Classify one KV listing; absence is provisional under KV eventual consistency."""
    keys = release_keys(production_key)
    prefix = f"{production_key}.release-v1."
    objects: dict[str, set[str]] = {}
    for name in names:
        if not name.startswith(prefix):
            continue
        match = _OBJECT.fullmatch(name[len(prefix) :])
        if match is not None:
            objects.setdefault(match.group(1), set()).add(match.group(2))
    protected = {
        item
        for item in (current_release_id, previous_release_id, production_release_id)
        if item is not None
    }
    observed = set(objects)
    return {
        "status": "observed_once",
        "production_key": keys.production,
        "mutation": "none",
        "protected_release_ids": sorted(protected),
        "journaled_release_ids": sorted(journal_ids),
        "unrecorded_release_ids": sorted(observed - journal_ids),
        "unrecorded_unprotected_release_ids": sorted(observed - journal_ids - protected),
        "incomplete_release_ids": sorted(
            release_id for release_id, kinds in objects.items() if kinds != {"config", "manifest"}
        ),
        "journaled_but_unlisted_release_ids": sorted(journal_ids - observed),
        "observed_release_objects": sum(len(kinds) for kinds in objects.values()),
        "review": "Repeat after KV propagation and recheck current, previous, and production before any cleanup.",
    }
