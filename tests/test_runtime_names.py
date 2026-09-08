from __future__ import annotations

import pytest

from clash_relay.generator import _runtime_name
from clash_relay.models import Node
from clash_relay.runtime_names import (
    canonical_source_id,
    runtime_source_label,
    validate_runtime_source_labels,
)


def _node(source_id: str) -> Node:
    return Node(
        source_id=source_id,
        source_display_name=source_id,
        source_ingest_order=100,
        source_allowed_uses=frozenset({"general"}),
        source_allowed_countries=frozenset({"*"}),
        original_name="Example Node",
        proxy={"name": "Example Node", "type": "http", "server": "example.invalid", "port": 443},
        country="US",
        capabilities=frozenset({"general"}),
        cost_level="normal",
        fingerprint="f" * 64,
    )


def test_numbered_subscription_ids_are_shortened_for_runtime_display() -> None:
    assert runtime_source_label("subscription_1") == "sub_1"
    assert runtime_source_label("subscription_27") == "sub_27"
    assert runtime_source_label("primary") == "primary"
    assert runtime_source_label("subscription_alpha") == "subscription_alpha"


def test_runtime_name_uses_short_label_but_keeps_original_name_and_digest() -> None:
    name = _runtime_name(_node("subscription_2"), "GENERAL:ANY")
    assert name.startswith("[GENERAL:ANY] sub_2/Example Node #")
    assert "subscription_2/" not in name


def test_short_label_resolves_back_to_canonical_source_id() -> None:
    known = {"subscription_1", "subscription_2"}
    assert canonical_source_id("sub_2", known) == "subscription_2"
    assert canonical_source_id("other", known) == "other"


def test_ambiguous_short_label_is_rejected_fail_closed() -> None:
    with pytest.raises(ValueError, match="share runtime label"):
        validate_runtime_source_labels({"subscription_1", "sub_1"})
