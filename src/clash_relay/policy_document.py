"""Load physical policy configuration into one canonical domain document.

Policy Model v1 is the deprecated historical monolithic ``policies.yaml``.
Policy Model v2 is a small manifest whose named domain fragments contribute
disjoint top-level policy sections. All consumers receive the same normalized
v1-shaped mapping, so physical file layout never leaks into routing/generation
logic.
"""

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


@dataclass(frozen=True, slots=True)
class PolicyDocument:
    model_version: int
    document: dict[str, Any]
    sources: tuple[Path, ...]
    deprecated: bool = False

    @property
    def compatibility_status(self) -> str:
        return "deprecated" if self.deprecated else "current"


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


def _validate_fragment_owner(fragment_name: str, section: str) -> None:
    expected = _SECTION_OWNERS.get(section)
    if expected is not None and fragment_name != expected:
        raise ConfigurationError(
            f"policy section {section!r} belongs to fragment {expected!r}, "
            f"not {fragment_name!r}"
        )


def load_policy_document(path: Path) -> PolicyDocument:
    """Load deprecated v1 or compose current v2, then validate canonical semantics."""

    raw = load_yaml_file(path)
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{path} must contain a policy mapping")

    if raw.get("version") != 2 or "fragments" not in raw:
        validate_schema(raw, "policies.schema.json", source=str(path))
        return PolicyDocument(
            model_version=1,
            document=raw,
            sources=(path.resolve(),),
            deprecated=True,
        )

    validate_schema(raw, "policy-manifest.schema.json", source=str(path))
    fragments = raw["fragments"]
    if not isinstance(fragments, dict):
        raise ConfigurationError("policy manifest fragments must be a mapping")

    merged: dict[str, Any] = {"version": 1}
    sources: list[Path] = [path.resolve()]
    owners: dict[str, str] = {}
    for fragment_name, relative in fragments.items():
        fragment_name = str(fragment_name)
        target = _safe_fragment_path(path, str(relative))
        fragment = load_yaml_file(target)
        if not isinstance(fragment, dict) or not fragment:
            raise ConfigurationError(
                f"policy fragment {fragment_name!r} must contain a non-empty mapping"
            )
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
    return PolicyDocument(
        model_version=2,
        document=merged,
        sources=tuple(sources),
        deprecated=False,
    )


def load_policies(path: Path) -> dict[str, Any]:
    """Compatibility helper for consumers that only need the normalized mapping."""

    return load_policy_document(path).document
