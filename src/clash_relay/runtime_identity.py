"""Canonical provider and runtime-group identity naming.

This module is the single source of truth for the generated identity of a
pool-region shard: the inline provider name and its hidden automatic group.
The generator creates providers through it and the qualification layers
resolve provider-to-region attribution through it, so qualification evidence
can never drift from production runtime identity.
"""

from __future__ import annotations

from .util import safe_identifier

_SCOPE_TOKEN_MAXIMUM = 36


def scope_token(value: str) -> str:
    """Canonical uppercase scope token for one pool or region identifier."""

    return safe_identifier(value, upper=True, maximum=_SCOPE_TOKEN_MAXIMUM)


def provider_name_for(unit_id: str, region: str) -> str:
    """Canonical inline provider name for one pool-region shard."""

    return f"cr_{safe_identifier(unit_id)}_{safe_identifier(region)}"


def automatic_group_name(unit_id: str, region: str) -> str:
    """Canonical hidden automatic group name for one pool-region shard."""

    return f"__CR_AUTO_{scope_token(unit_id)}_{scope_token(region)}"


def provider_and_group_names(unit_id: str, region: str) -> tuple[str, str]:
    """Return (provider_name, automatic_group_name) for one pool-region shard."""

    return provider_name_for(unit_id, region), automatic_group_name(unit_id, region)
