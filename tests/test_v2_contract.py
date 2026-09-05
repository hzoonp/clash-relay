from __future__ import annotations

import tomllib
from pathlib import Path

from clash_relay import __version__
from clash_relay.scheduler_policy import load_scheduler_policy


def test_v2_package_and_release_notes_are_aligned(repo_root: Path) -> None:
    with (repo_root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    notes = repo_root / "docs" / "releases" / f"{project['version']}.md"

    assert project["version"] == "2.0.1"
    assert __version__ == project["version"]
    assert "Development Status :: 5 - Production/Stable" in project["classifiers"]
    assert notes.is_file()
    assert "# clash-relay 2.0.1" in notes.read_text(encoding="utf-8")


def test_v2_contract_preserves_canonical_scheduler_defaults(repo_root: Path) -> None:
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


def test_v2_versioning_contract_names_nonnegotiable_boundaries(repo_root: Path) -> None:
    text = (repo_root / "docs" / "versioning.md").read_text(encoding="utf-8")

    for phrase in (
        "clean-slate public contract",
        "allowed_uses: [browsing, ai]",
        "strictly greater than `2.0`",
        "exactly `2.0` and unmarked nodes remain eligible",
        "ingest_order",
        "ProxyLite.list",
        "ProxyGFWlist",
        "Final `MATCH`",
        "US -> SG -> JP -> TW -> KR -> HK -> OTHER",
        "3/3 is Stable",
        "2/3 is Reserve",
        "Manual regional browsing choices never cross regions",
        "never promotes a current Reserve or live-failed node into Stable",
        "OpenAI, Claude, and Gemini",
        "ServiceQualification",
        "every stable Mihomo core declared in `tools/mihomo-versions.json`",
        "exact validated commit SHA",
        "storage schema version 1",
        "previous-v1` compatibility slot",
        "current source/Routing V2 policy audit",
        "not attached to GitHub Releases",
    ):
        assert phrase in text
