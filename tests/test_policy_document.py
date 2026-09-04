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


def test_canonical_policy_is_model_v2() -> None:
    loaded = load_policy_document(ROOT / "policies.yaml")

    assert loaded.model_version == 2
    assert loaded.document["version"] == 1
    assert loaded.document["routing"]["version"] == 2
    assert "scheduler" in loaded.document
    assert len(loaded.sources) == 5


def test_policy_model_v2_composes_to_the_same_domain_document(tmp_path: Path) -> None:
    canonical = load_policy_document(ROOT / "policies.yaml").document

    _write(
        tmp_path / "policies/runtime.yaml",
        {key: canonical[key] for key in ("scheduler", "routing") if key in canonical},
    )
    _write(
        tmp_path / "policies/classification.yaml",
        {key: canonical[key] for key in ("capabilities", "cost_levels", "country_classification")},
    )
    _write(tmp_path / "policies/qualification.yaml", {"probes": canonical["probes"]})
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
                "runtime": "policies/runtime.yaml",
                "classification": "policies/classification.yaml",
                "qualification": "policies/qualification.yaml",
                "topology": "policies/topology.yaml",
            },
        },
    )

    loaded = load_policy_document(manifest)

    assert loaded.model_version == 2
    assert loaded.document == canonical
    assert len(loaded.sources) == 5


def test_policy_model_v2_rejects_duplicate_sections(tmp_path: Path) -> None:
    _write(tmp_path / "a.yaml", {"scheduler": {}})
    _write(tmp_path / "b.yaml", {"scheduler": {}})
    manifest = tmp_path / "policies.yaml"
    _write(manifest, {"version": 2, "fragments": {"a": "a.yaml", "b": "b.yaml"}})

    with pytest.raises(ConfigurationError, match="declared by both"):
        load_policy_document(manifest)


def test_policy_model_v2_rejects_fragment_path_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-policy.yaml"
    _write(outside, {"scheduler": {}})
    manifest = tmp_path / "policies.yaml"
    _write(manifest, {"version": 2, "fragments": {"outside": "../outside-policy.yaml"}})

    with pytest.raises(ConfigurationError, match="escapes"):
        load_policy_document(manifest)
