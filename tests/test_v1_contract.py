from __future__ import annotations

import tomllib
from pathlib import Path

from clash_relay import __version__
from clash_relay.scheduler_policy import load_scheduler_policy


def test_v1_package_and_changelog_are_aligned(repo_root: Path) -> None:
    with (repo_root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    changelog = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")

    assert project["version"] == "1.7.0"
    assert __version__ == project["version"]
    assert "Development Status :: 5 - Production/Stable" in project["classifiers"]
    assert "## [1.7.0] - 2026-09-03" in changelog


def test_v1_contract_preserves_canonical_scheduler_defaults(repo_root: Path) -> None:
    policy = load_scheduler_policy(repo_root / "policies.yaml")

    assert policy.declared is True
    assert policy.browsing.attempts == 3
    assert policy.browsing.reserve_successes == 2
    assert policy.browsing.region_switch_interval == 300
    assert policy.history.min_runs == 2
    assert policy.history.min_success_ema == 0.8
    assert policy.history.recover_success_ema == 0.9
    assert policy.history.demote_after_failures == 2
    assert policy.history.max_age_seconds == 2592000
    assert policy.ai_cache.pass_ttl_seconds == 21600
    assert policy.ai_cache.failure_ttl_seconds == 3600
    assert policy.ai_cache.openai_pass_ttl_seconds == 7200
    assert policy.ai_cache.openai_failure_ttl_seconds == 3600


def test_v1_versioning_contract_names_nonnegotiable_boundaries(repo_root: Path) -> None:
    text = (repo_root / "docs" / "versioning.md").read_text(encoding="utf-8")

    for phrase in (
        "allowed_uses: [browsing, ai]",
        "strictly greater than `2.0`",
        "ProxyLite.list",
        "ProxyGFWlist",
        "Final `MATCH`",
        "US -> SG -> JP -> TW -> KR -> HK -> OTHER",
        "preferred-region Stable, then same-region Reserve, then the next available region",
        "manual regional browsing choice never crosses regions",
        "3/3 is Stable",
        "2/3 is Reserve",
        "historically demoted but currently qualified node moves to Reserve in the same region",
        "never promotes a current Reserve or live-failed node into Stable",
        "OpenAI, Claude, and Gemini",
        "BanProgramAD.list",
        "every stable Mihomo core declared in `tools/mihomo-versions.json`",
        "Manual rollback requires explicit confirmation",
        "not attached to GitHub Releases",
        "DNS resolutions containing private/special-use",
    ):
        assert phrase in text
