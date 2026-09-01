from __future__ import annotations

import json
from pathlib import Path

import pytest

from clash_relay.errors import ValidationError
from clash_relay.mihomo_matrix import load_mihomo_tags, primary_mihomo_tag

ROOT = Path(__file__).resolve().parents[1]


def test_stable_matrix_comes_from_version_manifest() -> None:
    manifest = ROOT / "tools" / "mihomo-versions.json"
    tags = load_mihomo_tags(manifest)
    assert tags
    assert primary_mihomo_tag(manifest) == tags[0]
    assert len(tags) == len(set(tags))


def test_matrix_rejects_duplicate_tags(tmp_path: Path) -> None:
    manifest = tmp_path / "versions.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repository": "example/repo",
                "stable": [{"tag": "v1"}, {"tag": "v1"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="duplicate"):
        load_mihomo_tags(manifest)
