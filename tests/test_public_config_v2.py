from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from clash_relay.errors import ConfigurationError
from clash_relay.schema import load_and_validate


def _document(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_canonical_public_declarations_are_v2_only(repo_root: Path) -> None:
    config = _document(repo_root / "config.yaml")
    subscriptions = _document(repo_root / "subscriptions.yaml")
    policies = _document(repo_root / "policies.yaml")

    assert config["version"] == 2
    assert subscriptions["version"] == 2
    assert policies["version"] == 2

    for row in subscriptions["subscriptions"]:
        assert "ingest_order" in row
        assert "priority" not in row

    subscription_1 = subscriptions["subscriptions"][0]
    assert subscription_1["id"] == "subscription_1"
    assert set(subscription_1["allowed_uses"]) == {"browsing", "ai"}
    assert subscription_1["max_node_multiplier"] == 2.0


def test_v1_config_is_rejected_without_runtime_compatibility(
    repo_root: Path, tmp_path: Path
) -> None:
    document = _document(repo_root / "config.yaml")
    document["version"] = 1
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="schema validation"):
        load_and_validate(path, "config.schema.json")


def test_v1_subscriptions_are_rejected_without_runtime_compatibility(
    repo_root: Path, tmp_path: Path
) -> None:
    document = _document(repo_root / "subscriptions.yaml")
    document["version"] = 1
    path = tmp_path / "subscriptions.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="schema validation"):
        load_and_validate(path, "subscriptions.schema.json")


def test_legacy_public_priority_field_is_rejected(repo_root: Path, tmp_path: Path) -> None:
    document = _document(repo_root / "subscriptions.yaml")
    row = document["subscriptions"][0]
    row["priority"] = row.pop("ingest_order")
    path = tmp_path / "subscriptions.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="schema validation"):
        load_and_validate(path, "subscriptions.schema.json")
