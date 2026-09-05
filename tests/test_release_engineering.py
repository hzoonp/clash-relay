from __future__ import annotations

import tomllib
from pathlib import Path

import yaml


def test_package_version_has_matching_versioned_release_notes(repo_root: Path) -> None:
    with (repo_root / "pyproject.toml").open("rb") as handle:
        version = tomllib.load(handle)["project"]["version"]
    notes = repo_root / "docs" / "releases" / f"{version}.md"
    assert notes.is_file()
    assert f"# clash-relay {version}" in notes.read_text(encoding="utf-8")


def test_release_workflow_is_source_only_and_exact_sha_bound(repo_root: Path) -> None:
    path = repo_root / ".github" / "workflows" / "release.yml"
    text = path.read_text(encoding="utf-8")
    workflow = yaml.load(text, Loader=yaml.BaseLoader)
    assert isinstance(workflow, dict)
    assert workflow["permissions"]["contents"] == "write"
    assert "needs.validate.outputs.validated_sha == github.sha" in text
    assert "ref: ${{ needs.validate.outputs.validated_sha }}" in text
    assert "docs/releases/${VERSION}.md" in text
    assert "gh release create" in text
    assert "--notes-file .release-notes.md" in text
    assert "actions/upload-artifact" not in text
    assert "config.yaml" not in text
    assert "scheduler-history" not in text
    assert "CLOUDFLARE_API_TOKEN" not in text
    assert "CLASH_RELAY_SUBSCRIPTIONS" not in text


def test_versioning_document_freezes_canonical_v2_boundaries(repo_root: Path) -> None:
    text = (repo_root / "docs" / "versioning.md").read_text(encoding="utf-8")
    assert "clean-slate public contract" in text
    assert "allowed_uses: [browsing, ai]" in text
    assert "strictly greater than `2.0`" in text
    assert "ingest_order" in text
    assert "ProxyGFWlist" in text
    assert "MATCH" in text
    assert "3/3" in text
    assert "2/3" in text
    assert "tools/mihomo-versions.json" in text
    assert "routing.contract" in text
    assert "current-release-v1" in text
    assert "storage schema version 1" in text
    assert "previous-v1` compatibility slot" in text
    assert "current source/Routing V2 policy audit" in text
    assert "not attached to GitHub Releases" in text
