from __future__ import annotations

from pathlib import Path


def test_unused_publisher_protocol_stays_removed(repo_root: Path) -> None:
    assert not (repo_root / "src" / "clash_relay" / "publishers" / "base.py").exists()


def test_legacy_gist_backend_cannot_enter_production_release_path(repo_root: Path) -> None:
    for relative in (
        "src/clash_relay/production_application.py",
        "src/clash_relay/production_lifecycle.py",
        "src/clash_relay/production_release_stage.py",
        "src/clash_relay/release_bundle.py",
        "scripts/run_production_release.py",
    ):
        content = (repo_root / relative).read_text(encoding="utf-8")
        assert "publishers.gist" not in content
        assert "GistPublisher" not in content
