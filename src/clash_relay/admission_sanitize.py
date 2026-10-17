"""Shared privacy-safe projections of admission counters.

Production metrics and the production proof both publish
``skipped_invalid_reasons`` counters. The two reports keep their own field
whitelists and independent responsibilities, but the counter projection must
stay byte-identical, so it lives here once.
"""

from __future__ import annotations

import re
from typing import Any, TypeGuard

_REASON = re.compile(r"[a-z0-9_]{1,64}")
_SOURCE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def safe_admission_reason_counts(value: Any) -> dict[str, int]:
    """Return only ``{safe_reason: non-negative count}`` pairs, sorted by reason."""

    if not isinstance(value, dict):
        return {}
    clean: dict[str, int] = {}
    for reason, count in sorted(value.items()):
        if (
            isinstance(reason, str)
            and _REASON.fullmatch(reason)
            and isinstance(count, int)
            and not isinstance(count, bool)
            and count >= 0
        ):
            clean[reason] = count
    return clean


def is_safe_source_id(value: Any) -> TypeGuard[str]:
    """Whether a source identifier may appear in a published aggregate."""

    return isinstance(value, str) and _SOURCE_ID.fullmatch(value) is not None
