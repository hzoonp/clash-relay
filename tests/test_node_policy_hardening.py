from __future__ import annotations

import pytest

from clash_relay.node_policy import filter_proxies_by_multiplier, node_name_multiplier


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("HK 2x", 2.0),
        ("JP 2.0X", 2.0),
        ("SG x2", 2.0),
        ("US x2.5", 2.5),
        ("TW 倍率=2", 2.0),
        ("KR 倍率=2.5", 2.5),
        ("HK 2倍", 2.0),
        ("linux x64", None),
        ("core X86_64", None),
        ("amd64", None),
        ("ordinary", None),
    ],
)
def test_multiplier_parser_distinguishes_explicit_markers_from_architecture(
    name: str, expected: float | None
) -> None:
    assert node_name_multiplier(name) == expected


def test_multiplier_ceiling_remains_strictly_greater_than_two() -> None:
    proxies = [
        {"name": "keep 2x"},
        {"name": "keep x64"},
        {"name": "drop 2.01x"},
        {"name": "drop 倍率=3"},
    ]

    kept, rejected = filter_proxies_by_multiplier(proxies, max_multiplier=2.0)

    assert [item["name"] for item in kept] == ["keep 2x", "keep x64"]
    assert rejected == 2
