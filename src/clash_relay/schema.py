"""JSON Schema validation helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .errors import ConfigurationError, ValidationError
from .util import load_yaml_file

_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"


def load_schema(name: str) -> dict[str, Any]:
    import json

    path = _SCHEMA_DIR / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"cannot load schema {name}") from exc


def validate_schema(instance: Any, schema_name: str, *, source: str, output: bool = False) -> None:
    validator = Draft202012Validator(load_schema(schema_name), format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.absolute_path))
    if not errors:
        return
    lines: list[str] = []
    for error in errors[:20]:
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        lines.append(f"{location}: {error.message}")
    suffix = "" if len(errors) <= 20 else f"; plus {len(errors) - 20} more"
    exception = ValidationError if output else ConfigurationError
    raise exception(f"{source} failed schema validation: {'; '.join(lines)}{suffix}")


def load_and_validate(path: Path, schema_name: str) -> Any:
    data = load_yaml_file(path)
    validate_schema(data, schema_name, source=str(path))
    return data
