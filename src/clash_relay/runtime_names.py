"""Safe presentation aliases for generated runtime proxy names."""

from __future__ import annotations

import re
from collections.abc import Iterable

_LONG_SOURCE = re.compile(r"^subscription_([0-9]+)$")
_SOURCE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_RUNTIME_NAME = re.compile(
    r"^\[(?P<scope>[A-Z][A-Z0-9:_-]*)\] (?P<source>[a-z][a-z0-9_-]{0,63})/.+ #[0-9a-f]{10}"
    r"(?: \[OAI:[0-9a-f]{8}\])?$"
)


def valid_source_id(value: object) -> bool:
    """Whether a source ID matches the subscription schema's public identifier."""
    return isinstance(value, str) and _SOURCE_ID.fullmatch(value) is not None


def runtime_source_label(source_id: str) -> str:
    """Shorten canonical numbered subscription ids without changing policy identity."""
    if not valid_source_id(source_id):
        raise ValueError("invalid subscription source id")
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
    if not valid_source_id(runtime_label):
        raise ValueError("invalid runtime source label")
    mapping = validate_runtime_source_labels(known_source_ids)
    try:
        return mapping[runtime_label]
    except KeyError as exc:
        raise ValueError("unknown runtime source label") from exc


def parse_runtime_source_name(name: object) -> str | None:
    """Read only the source slot in a generated runtime proxy name."""
    if not isinstance(name, str):
        return None
    match = _RUNTIME_NAME.fullmatch(name)
    return match.group("source") if match is not None else None


def parse_runtime_name_region(name: object) -> str | None:
    """Region label from a runtime name's scope suffix (``[AI_JP:JP]`` -> ``JP``).

    The scope grammar is owned by the generator; this accessor reads the region
    segment after the last colon without any second parsing convention. Pool
    scopes without a region segment (e.g. chains) project their scope token.
    """

    if not isinstance(name, str):
        return None
    match = _RUNTIME_NAME.fullmatch(name)
    if match is None:
        return None
    scope = match.group("scope")
    return scope.split(":", 1)[-1] if ":" in scope else scope
