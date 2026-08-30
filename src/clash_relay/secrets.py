"""Resolve subscription URLs without ever putting them in public declarations."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .errors import SecretError
from .models import SubscriptionSpec
from .util import yaml_load_no_aliases


def _normalize_mapping(value: Any, *, source: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SecretError(f"{source} must contain a mapping of secret names to URLs")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise SecretError(f"{source} contains an invalid secret name")
        if isinstance(item, dict):
            item = item.get("url")
        if not isinstance(item, str) or not item.strip():
            raise SecretError(f"{source} entry {key!r} must be a non-empty URL string")
        result[key] = item.strip()
    return result


def _parse_mapping_text(text: str, *, source: str) -> dict[str, str]:
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        decoded = yaml_load_no_aliases(text, source=source)
    return _normalize_mapping(decoded, source=source)


def load_secret_mapping(
    secret_file: Path | None = None, env: Mapping[str, str] | None = None
) -> dict[str, str]:
    environment = os.environ if env is None else env
    result: dict[str, str] = {}
    bundle = environment.get("CLASH_RELAY_SUBSCRIPTIONS")
    if bundle:
        result.update(_parse_mapping_text(bundle, source="CLASH_RELAY_SUBSCRIPTIONS"))
    if secret_file is not None:
        try:
            text = secret_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise SecretError("cannot read the local secret file") from exc
        result.update(_parse_mapping_text(text, source="local secret file"))
    return result


def resolve_subscription_urls(
    specs: list[SubscriptionSpec],
    *,
    secret_file: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], tuple[str, ...]]:
    environment = os.environ if env is None else env
    mapping = load_secret_mapping(secret_file, environment)
    resolved: dict[str, str] = {}
    missing: list[str] = []
    secret_values: list[str] = list(mapping.values())
    for spec in specs:
        if not spec.enabled:
            continue
        value = mapping.get(spec.secret_name) or environment.get(spec.secret_name)
        if not value or not value.strip():
            missing.append(spec.secret_name)
            continue
        resolved[spec.id] = value.strip()
        secret_values.append(value.strip())
    if missing:
        names = ", ".join(sorted(set(missing)))
        raise SecretError(f"missing subscription URL secret(s): {names}")
    return resolved, tuple(dict.fromkeys(secret_values))
