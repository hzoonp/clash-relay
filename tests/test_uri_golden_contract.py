from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from clash_relay.subscription_parser import parse_subscription

FIXTURES = Path(__file__).parent / "fixtures" / "uri"


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.yaml")), ids=lambda path: path.stem)
def test_uri_semantics_survive_sanitization_and_yaml_round_trip(path: Path) -> None:
    fixture = yaml.safe_load(path.read_text(encoding="utf-8"))
    proxy = parse_subscription(fixture["uri"]).proxies[0]
    serialized = yaml.safe_load(yaml.safe_dump(proxy, sort_keys=True))

    for field, expected in fixture["expected"].items():
        assert proxy[field] == expected
        assert serialized[field] == expected
