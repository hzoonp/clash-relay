from __future__ import annotations

from pathlib import Path

import pytest

from clash_relay.browsing_regions import (
    region_display_name,
    region_reserve_group,
    region_stable_group,
)
from clash_relay.browsing_runtime import (
    BROWSING_AUTO_GROUP,
    BROWSING_PUBLIC_GROUP,
    harden_browsing_runtime,
    rewrite_hardened_browsing_qualified_candidate,
)
from clash_relay.errors import ValidationError
from clash_relay.util import dump_yaml, load_yaml_file
from clash_relay.validator import validate_generated_config


def _probe_fields() -> dict[str, object]:
    return {
        "url": "https://www.gstatic.com/generate_204",
        "interval": 180,
        "timeout": 3000,
        "lazy": False,
        "expected-status": 204,
        "tolerance": 150,
    }


def _provider(name: str) -> dict[str, object]:
    return {
        "type": "inline",
        "health-check": {
            "enable": True,
            "url": "https://www.gstatic.com/generate_204",
            "interval": 180,
            "timeout": 3000,
            "lazy": False,
            "expected-status": 204,
        },
        "payload": [{"name": name, "type": "direct"}],
    }


def _compiler_auto(region: str) -> str:
    return f"__CR_AUTO_BROWSING_{region}"


def _production_shaped_candidate() -> dict[str, object]:
    regions = ("US", "SG", "JP")
    compiler_auto = [_compiler_auto(region) for region in regions]
    groups: list[dict[str, object]] = [
        {
            "name": compiler_auto[index],
            "type": "url-test",
            "hidden": True,
            "use": [f"cr_browsing_{region.lower()}"],
            **_probe_fields(),
        }
        for index, region in enumerate(regions)
    ]
    groups.extend(
        [
            {
                "name": "__CR_FALLBACK_BROWSING",
                "type": "fallback",
                "hidden": True,
                "proxies": list(compiler_auto),
                **_probe_fields(),
            },
            {
                "name": "__CR_BROWSING_INVENTORY",
                "type": "select",
                "hidden": True,
                "proxies": ["__CR_FALLBACK_BROWSING"],
            },
            {
                "name": BROWSING_AUTO_GROUP,
                "type": "url-test",
                "hidden": True,
                "use": [f"cr_browsing_{region.lower()}" for region in regions],
                "filter": ".*",
                **_probe_fields(),
            },
            {
                "name": BROWSING_PUBLIC_GROUP,
                "type": "select",
                "proxies": [BROWSING_AUTO_GROUP, "DIRECT"],
                "use": [f"cr_browsing_{region.lower()}" for region in regions],
                "filter": ".*",
            },
        ]
    )
    return {
        "mixed-port": 7890,
        "mode": "rule",
        "proxy-providers": {
            "cr_browsing_us": _provider("us-ok"),
            "cr_browsing_sg": _provider("sg-drop"),
            "cr_browsing_jp": _provider("jp-ok"),
        },
        "proxy-groups": groups,
        "rules": [f"MATCH,{BROWSING_PUBLIC_GROUP}"],
    }


def _policies() -> dict[str, object]:
    regions = ["US", "SG", "JP"]
    return {
        "scheduler": {"browsing": {"region_switch_interval": 300}},
        "routing": {"browsing": {"preferred_regions": regions}},
        "pools": [
            {
                "id": "browsing",
                "probe": "browsing",
                "regions": regions,
                "fallback_order": regions,
            }
        ],
        "probes": {
            "browsing": {
                "url": "https://www.gstatic.com/generate_204",
                "expected_status": "204",
                "interval": 180,
                "timeout": 3000,
                "lazy": False,
                "tolerance": 150,
            }
        },
    }


def _groups(document: dict[str, object]) -> dict[str, dict[str, object]]:
    rows = document["proxy-groups"]
    assert isinstance(rows, list)
    return {
        str(row["name"]): row
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    }


def _write_hardened_candidate(tmp_path: Path) -> Path:
    document = _production_shaped_candidate()
    harden_browsing_runtime(document, _policies())
    candidate = tmp_path / "config.yaml"
    candidate.write_text(dump_yaml(document), encoding="utf-8")
    return candidate


def test_region_prune_removes_stale_compiler_anchor_and_fallback_reference(
    tmp_path: Path,
) -> None:
    candidate = _write_hardened_candidate(tmp_path)

    report = rewrite_hardened_browsing_qualified_candidate(
        candidate,
        qualified_names={"us-ok", "jp-ok"},
        stable_names={"us-ok", "jp-ok"},
    )

    rewritten = load_yaml_file(candidate)
    assert isinstance(rewritten, dict)
    groups = _groups(rewritten)
    providers = rewritten["proxy-providers"]
    assert isinstance(providers, dict)

    assert report["removed_regions"] == ["SG"]
    assert "cr_browsing_sg" not in providers
    assert _compiler_auto("SG") not in groups
    assert groups["__CR_FALLBACK_BROWSING"]["proxies"] == [
        _compiler_auto("US"),
        _compiler_auto("JP"),
    ]
    assert region_display_name("SG") not in groups
    assert region_stable_group("SG") not in groups
    assert region_reserve_group("SG") not in groups
    assert all(
        "cr_browsing_sg" not in group.get("use", [])
        for group in groups.values()
        if isinstance(group.get("use", []), list)
    )
    validate_generated_config(rewritten)


def test_multiple_region_prune_keeps_only_surviving_compiler_anchor(tmp_path: Path) -> None:
    candidate = _write_hardened_candidate(tmp_path)

    report = rewrite_hardened_browsing_qualified_candidate(
        candidate,
        qualified_names={"jp-ok"},
        stable_names={"jp-ok"},
    )

    rewritten = load_yaml_file(candidate)
    assert isinstance(rewritten, dict)
    groups = _groups(rewritten)

    assert report["available_regions"] == ["JP"]
    assert report["removed_regions"] == ["US", "SG"]
    assert groups["__CR_FALLBACK_BROWSING"]["proxies"] == [_compiler_auto("JP")]
    assert groups[BROWSING_AUTO_GROUP]["proxies"] == [region_display_name("JP")]
    assert groups[BROWSING_PUBLIC_GROUP]["proxies"] == [
        BROWSING_AUTO_GROUP,
        region_display_name("JP"),
        "DIRECT",
    ]
    validate_generated_config(rewritten)


def test_region_prune_rejects_unexpected_direct_provider_reference(tmp_path: Path) -> None:
    document = _production_shaped_candidate()
    document["proxy-groups"].append(
        {
            "name": "unexpected browsing provider consumer",
            "type": "select",
            "hidden": True,
            "use": ["cr_browsing_sg"],
            "filter": ".*",
        }
    )
    harden_browsing_runtime(document, _policies())
    candidate = tmp_path / "config.yaml"
    candidate.write_text(dump_yaml(document), encoding="utf-8")

    with pytest.raises(ValidationError, match="remains referenced"):
        rewrite_hardened_browsing_qualified_candidate(
            candidate,
            qualified_names={"us-ok", "jp-ok"},
            stable_names={"us-ok", "jp-ok"},
        )
