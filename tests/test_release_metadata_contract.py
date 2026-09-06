from __future__ import annotations

import re
import tomllib
from pathlib import Path


def _project_version(repo_root: Path) -> str:
    with (repo_root / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def test_current_version_has_release_notes_and_changelog_entry(repo_root: Path) -> None:
    version = _project_version(repo_root)
    notes = repo_root / "docs" / "releases" / f"{version}.md"
    changelog = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")

    assert notes.is_file()
    assert notes.read_text(encoding="utf-8").startswith(f"# clash-relay {version}\n")
    assert re.search(rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", changelog, re.M)


def test_unreleased_compare_starts_from_current_project_version(repo_root: Path) -> None:
    version = _project_version(repo_root)
    changelog = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")

    expected = (
        "[Unreleased]: https://github.com/hzoonp/clash-relay/compare/"
        f"v{version}...HEAD"
    )
    assert expected in changelog


def test_recent_v2_release_notes_are_indexed_in_changelog(repo_root: Path) -> None:
    changelog = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")

    for version in ("2.0.0", "2.1.0"):
        assert f"## [{version}]" in changelog
        assert f"[{version}]: https://github.com/hzoonp/clash-relay/" in changelog
