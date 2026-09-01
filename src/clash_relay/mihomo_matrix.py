"""Single source of truth for pinned Mihomo validation channels."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import ValidationError


def load_mihomo_tags(manifest: Path, channel: str = "stable") -> tuple[str, ...]:
    try:
        document: Any = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError("failed to load pinned Mihomo version manifest") from exc
    if not isinstance(document, dict) or int(document.get("schema_version", 0)) != 1:
        raise ValidationError("pinned Mihomo version manifest has an unsupported schema")
    entries = document.get(channel)
    if not isinstance(entries, list) or not entries:
        raise ValidationError(f"pinned Mihomo channel {channel!r} must not be empty")
    tags: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("tag"), str) or not entry["tag"]:
            raise ValidationError(f"pinned Mihomo channel {channel!r} contains an invalid tag")
        tags.append(str(entry["tag"]))
    if len(tags) != len(set(tags)):
        raise ValidationError(f"pinned Mihomo channel {channel!r} contains duplicate tags")
    return tuple(tags)


def primary_mihomo_tag(manifest: Path, channel: str = "stable") -> str:
    return load_mihomo_tags(manifest, channel)[0]
