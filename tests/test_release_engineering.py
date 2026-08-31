from __future__ import annotations

import tomllib
from pathlib import Path

import yaml


def test_package_version_has_matching_changelog_release(repo_root: Path) -> None:
    with (repo_root / "pyproject.toml").open("rb") as handle:
        version = tomllib.load(handle)["project"]["version"]
    changelog = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{version}]" in changelog


def test_release_workflow_is_source_only(repo_root: Path) -> None:
    path = repo_root / ".github" / "workflows" / "release.yml"
    text = path.read_text(encoding="utf-8")
    workflow = yaml.load(text, Loader=yaml.BaseLoader)
    assert isinstance(workflow, dict)
    assert workflow["permissions"]["contents"] == "write"
    assert "gh release create" in text
    assert "--notes-file .release-notes.md" in text
    assert "actions/upload-artifact" not in text
    assert "config.yaml" not in text
    assert "scheduler-history" not in text
    assert "CLOUDFLARE_API_TOKEN" not in text
    assert "CLASH_RELAY_SUBSCRIPTIONS" not in text


def test_versioning_document_freezes_canonical_boundaries(repo_root: Path) -> None:
    text = (repo_root / "docs" / "versioning.md").read_text(encoding="utf-8")
    assert "allowed_uses: [browsing, ai]" in text
    assert "strictly greater than `2.0`" in text
    assert "ProxyGFWlist" in text
    assert "MATCH" in text
    assert "3/3" in text
    assert "2/3" in text
    assert "v1.19.30" in text
    assert "v1.19.29" in text
    assert "not attached to GitHub Releases" in text
