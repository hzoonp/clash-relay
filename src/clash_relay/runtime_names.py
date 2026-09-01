"""Safe presentation aliases for generated runtime proxy names."""

from __future__ import annotations

import re
from collections.abc import Iterable

_LONG_SOURCE = re.compile(r"^subscription_([0-9]+)$")


def runtime_source_label(source_id: str) -> str:
    """Shorten canonical numbered subscription ids without changing policy identity."""
    match = _LONG_SOURCE.fullmatch(source_id)
    return f"sub_{match.group(1)}" if match is not None else source_id


def validate_runtime_source_labels(source_ids: Iterable[str]) -> dict[str, str]:
    """Return label-to-canonical-id mapping and reject ambiguous display aliases."""
    mapping: dict[str, str] = {}
    for source_id in sorted(set(source_ids)):
        label = runtime_source_label(source_id)
        previous = mapping.get(label)
        if previous is not None and previous != source_id:
            raise ValueError(
                f"subscription sources {previous!r} and {source_id!r} share runtime label {label!r}"
            )
        mapping[label] = source_id
    return mapping


def canonical_source_id(runtime_label: str, known_source_ids: Iterable[str]) -> str:
    """Resolve a generated display alias back to the canonical subscription id."""
    return validate_runtime_source_labels(known_source_ids).get(runtime_label, runtime_label)
