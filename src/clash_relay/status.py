"""Mihomo health-check expected-status validation."""

from __future__ import annotations

import re

from .errors import ConfigurationError

_SEGMENT_RE = re.compile(r"^(?P<start>[1-5][0-9]{2})(?:-(?P<end>[1-5][0-9]{2}))?$")


def parse_expected_status(value: str) -> frozenset[int]:
    """Parse slash/comma/space separated status codes and inclusive ranges."""
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError("expected_status must be a non-empty string")
    values: set[int] = set()
    segments = [part for part in re.split(r"[/, ]+", value.strip()) if part]
    for segment in segments:
        match = _SEGMENT_RE.fullmatch(segment)
        if not match:
            raise ConfigurationError(f"invalid expected_status segment: {segment}")
        start = int(match.group("start"))
        end = int(match.group("end") or start)
        if end < start or end - start > 200:
            raise ConfigurationError(f"invalid expected_status range: {segment}")
        values.update(range(start, end + 1))
    return frozenset(values)


def status_allowed(value: str, status: int) -> bool:
    return status in parse_expected_status(value)
