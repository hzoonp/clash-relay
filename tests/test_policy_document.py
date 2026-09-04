from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from clash_relay.errors import ConfigurationError
from clash_relay.policy_document import load_policy_document

ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")


def test_canonical_policy_is_current_model_v2() -> None:
    loaded = load_policy_document(ROOT / "policies.yaml")

    assert loaded.model_version == 2
    assert loaded.compatibility_status == "current"
    assert loaded.deprecated is False
    assert loaded.document["version"] == 1
    assert loaded.document["routing"]["version"] == 2
    assert "scheduler" in loaded.document
    assert len(loaded.sources) == 5


def test_policy_model_v2_composes_to_the_same_domain_document(tmp_path: Path) -> None:
    canonical = load_policy_document(ROOT / "policies.yaml").document

    _write(tmp_path / "policies/routing.yaml", {"routing": canonical["routing"]})
    _write(
        tmp_path / "policies/scheduling.yaml",
        {key: canonical[key] for key in ("scheduler", "probes")},
    )
    _write(
        tmp_path / "policies/classification.yaml",
        {key: canonical[key] for key in ("capabilities", "cost_levels", "country_classification")},
    )
    _write(
        tmp_path / "policies/topology.yaml",
        {key: canonical[key] for key in ("pools", "chains")},
    )
    manifest = tmp_path / "policies.yaml"
    _write(
        manifest,
        {
            "version": 2,
            "fragments": {
                "routing": "policies/routing.yaml",
                "scheduling": "policies/scheduling.yaml",
                "classification": "policies/classification.yaml",
                "topology": "policies/topology.yaml",
            },
        },
    )

    loaded = load_policy_document(manifest)

    assert loaded.model_version == 2
    assert loaded.compatibility_status == "current"
    assert loaded.document == canonical
    assert len(loaded.sources) == 5


def test_policy_model_v2_rejects_duplicate_sections(tmp_path: Path) -> None:
    _write(tmp_path / "a.yaml", {"future_extension": {"a": 1}})
    _write(tmp_path / "b.yaml", {"future_extension": {"b": 2}})
    manifest = tmp_path / "policies.yaml"
    _write(manifest, {"version": 2, "fragments": {"a": "a.yaml", "b": "b.yaml"}})

    with pytest.raises(ConfigurationError, match="declared by both"):
        load_policy_document(manifest)


def test_policy_model_v2_rejects_known_section_in_wrong_domain(tmp_path: Path) -> None:
    _write(tmp_path / "policies/routing.yaml", {"probes": {}})
    manifest = tmp_path / "policies.yaml"
    _write(
        manifest,
        {"version": 2, "fragments": {"routing": "policies/routing.yaml"}},
    )

    with pytest.raises(ConfigurationError, match="belongs to fragment 'scheduling'"):
        load_policy_document(manifest)


def test_policy_model_v2_rejects_fragment_path_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-policy.yaml"
    _write(outside, {"future_extension": {"enabled": True}})
    manifest = tmp_path / "policies.yaml"
    _write(manifest, {"version": 2, "fragments": {"outside": "../outside-policy.yaml"}})

    with pytest.raises(ConfigurationError, match="escapes"):
        load_policy_document(manifest)


def test_policy_model_v1_remains_readable_but_is_deprecated(tmp_path: Path) -> None:
    canonical = load_policy_document(ROOT / "policies.yaml").document
    legacy = tmp_path / "policies.yaml"
    _write(legacy, canonical)

    loaded = load_policy_document(legacy)

    assert loaded.model_version == 1
    assert loaded.deprecated is True
    assert loaded.compatibility_status == "deprecated"
    assert loaded.document == canonical
