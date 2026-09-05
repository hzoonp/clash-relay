"""Compose the canonical Policy Model v2 manifest into one domain document."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigurationError
from .schema import validate_schema
from .util import load_yaml_file

_SECTION_OWNERS = {
    "routing": "routing",
    "scheduler": "scheduling",
    "probes": "scheduling",
    "capabilities": "classification",
    "cost_levels": "classification",
    "country_classification": "classification",
    "pools": "topology",
    "chains": "topology",
}
_REQUIRED_FRAGMENTS = frozenset(("routing", "scheduling", "classification", "topology"))


@dataclass(frozen=True, slots=True)
class PolicyDocument:
    document: dict[str, Any]
    sources: tuple[Path, ...]
    model_version: int = 2


def _safe_fragment_path(manifest: Path, relative: str) -> Path:
    root = manifest.resolve().parent
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ConfigurationError(
            f"policy manifest fragment escapes the project root: {relative}"
        ) from exc
    if not target.is_file():
        raise ConfigurationError(f"policy manifest fragment does not exist: {relative}")
    return target


def policy_fragment_path(manifest: Path, fragment_name: str) -> Path:
    """Resolve one declared v2 fragment without admitting a legacy policy shape."""

    raw = load_yaml_file(manifest)
    if not isinstance(raw, dict) or raw.get("version") != 2:
        raise ConfigurationError("Policy Model v2 manifest is required")
    fragments = raw.get("fragments")
    if not isinstance(fragments, dict) or fragment_name not in _REQUIRED_FRAGMENTS:
        raise ConfigurationError(f"Policy Model v2 fragment {fragment_name!r} is not declared")
    relative = fragments.get(fragment_name)
    if not isinstance(relative, str) or not relative:
        raise ConfigurationError(f"Policy Model v2 fragment {fragment_name!r} has an invalid path")
    return _safe_fragment_path(manifest, relative)


def _validate_fragment_owner(fragment_name: str, section: str) -> None:
    expected = _SECTION_OWNERS.get(section)
    if expected is not None and fragment_name != expected:
        raise ConfigurationError(
            f"policy section {section!r} belongs to fragment {expected!r}, not {fragment_name!r}"
        )


def load_policy_document(path: Path) -> PolicyDocument:
    """Load the only supported runtime policy format: Policy Model v2."""

    raw = load_yaml_file(path)
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{path} must contain a policy mapping")
    if raw.get("version") != 2 or "fragments" not in raw:
        raise ConfigurationError(
            "Policy Model v2 manifest is required; migrate legacy policy files with "
            "scripts/migrate_policy_v2.py before running clash-relay"
        )

    validate_schema(raw, "policy-manifest.schema.json", source=str(path))
    fragments = raw["fragments"]
    if not isinstance(fragments, dict):
        raise ConfigurationError("policy manifest fragments must be a mapping")
    if set(fragments) != _REQUIRED_FRAGMENTS:
        raise ConfigurationError(
            "policy manifest must declare exactly these fragments: "
            + ", ".join(sorted(_REQUIRED_FRAGMENTS))
        )

    # The semantic schema predates the physical v2 split and retains version: 1
    # as its internal normalized-document marker. This is not an accepted input
    # format: callers can enter only through the v2 manifest above.
    merged: dict[str, Any] = {"version": 1}
    sources: list[Path] = [path.resolve()]
    owners: dict[str, str] = {}
    for fragment_name, relative in fragments.items():
        fragment_name = str(fragment_name)
        target = _safe_fragment_path(path, str(relative))
        fragment = load_yaml_file(target)
        if not isinstance(fragment, dict):
            raise ConfigurationError(f"policy fragment {fragment_name!r} must contain a mapping")
        if "version" in fragment or "fragments" in fragment:
            raise ConfigurationError(
                f"policy fragment {fragment_name!r} must not declare version/fragments"
            )
        for key, value in fragment.items():
            if not isinstance(key, str) or not key:
                raise ConfigurationError(
                    f"policy fragment {fragment_name!r} contains an invalid top-level key"
                )
            _validate_fragment_owner(fragment_name, key)
            previous = owners.get(key)
            if previous is not None:
                raise ConfigurationError(
                    f"policy section {key!r} is declared by both {previous!r} and {fragment_name!r}"
                )
            owners[key] = fragment_name
            merged[key] = value
        sources.append(target)

    validate_schema(
        merged,
        "policies.schema.json",
        source=f"{path} (composed policy model v2)",
    )
    return PolicyDocument(document=merged, sources=tuple(sources))


def load_policies(path: Path) -> dict[str, Any]:
    """Compose the canonical v2 policy manifest for domain consumers."""

    return load_policy_document(path).document
