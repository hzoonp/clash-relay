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


def _manifest(tmp_path: Path, *, routing: str = "policies/routing.yaml") -> Path:
    manifest = tmp_path / "policies.yaml"
    _write(
        manifest,
        {
            "version": 2,
            "fragments": {
                "routing": routing,
                "scheduling": "policies/scheduling.yaml",
                "classification": "policies/classification.yaml",
                "topology": "policies/topology.yaml",
            },
        },
    )
    return manifest


def test_canonical_policy_is_current_model_v2() -> None:
    loaded = load_policy_document(ROOT / "policies.yaml")

    assert loaded.model_version == 2
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
    manifest = _manifest(tmp_path)

    loaded = load_policy_document(manifest)

    assert loaded.model_version == 2
    assert loaded.document == canonical
    assert len(loaded.sources) == 5


def test_policy_model_v2_rejects_known_section_in_wrong_domain(tmp_path: Path) -> None:
    _write(tmp_path / "policies/routing.yaml", {"probes": {}})
    _write(tmp_path / "policies/scheduling.yaml", {})
    _write(tmp_path / "policies/classification.yaml", {})
    _write(tmp_path / "policies/topology.yaml", {})
    manifest = _manifest(tmp_path)

    with pytest.raises(ConfigurationError, match="belongs to fragment 'scheduling'"):
        load_policy_document(manifest)


def test_policy_model_v2_rejects_fragment_path_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-policy.yaml"
    _write(outside, {})
    _write(tmp_path / "policies/scheduling.yaml", {})
    _write(tmp_path / "policies/classification.yaml", {})
    _write(tmp_path / "policies/topology.yaml", {})
    manifest = _manifest(tmp_path, routing="../outside-policy.yaml")

    with pytest.raises(ConfigurationError, match="escapes"):
        load_policy_document(manifest)


def test_policy_model_v2_requires_exact_domain_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "policies.yaml"
    _write(manifest, {"version": 2, "fragments": {"routing": "routing.yaml"}})

    with pytest.raises(ConfigurationError, match="schema validation"):
        load_policy_document(manifest)


def test_policy_model_v1_is_rejected_with_offline_migration_guidance(tmp_path: Path) -> None:
    legacy = tmp_path / "policies.yaml"
    _write(legacy, load_policy_document(ROOT / "policies.yaml").document)

    with pytest.raises(ConfigurationError, match="migrate_policy_v2.py"):
        load_policy_document(legacy)
