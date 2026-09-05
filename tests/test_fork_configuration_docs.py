from __future__ import annotations

from pathlib import Path


def test_quickstarts_surface_minimal_configuration_guides(repo_root: Path) -> None:
    english = (repo_root / "docs/quickstart.md").read_text(encoding="utf-8")
    chinese = (repo_root / "docs/quickstart.zh-CN.md").read_text(encoding="utf-8")

    assert "[Fork configuration surface](fork-configuration.md)" in english
    assert "[Fork 配置边界](fork-configuration.zh-CN.md)" in chinese
    assert "three things" in english
    assert "三件事" in chinese


def test_configuration_guides_keep_routine_and_advanced_surfaces_separate(
    repo_root: Path,
) -> None:
    documents = [
        (repo_root / "docs/fork-configuration.md").read_text(encoding="utf-8"),
        (repo_root / "docs/fork-configuration.zh-CN.md").read_text(encoding="utf-8"),
    ]

    for document in documents:
        assert "CLASH_RELAY_SUBSCRIPTIONS" in document
        assert "subscriptions.yaml" in document
        assert "policies/routing.yaml" in document
        assert "policies/topology.yaml" in document
        assert "promotion-guard.yaml" in document
        assert "tools/mihomo-versions.json" in document
        assert "subscription_1" in document
        assert "> 2x" in document
        assert "ingest_order" in document
        assert "https://" not in document
