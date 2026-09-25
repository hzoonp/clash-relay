"""Regression tests for systemic OpenAI probe-environment isolation.

Every scenario runs against a candidate generated through the real canonical
pipeline (``build_candidate`` over the pinned Policy Model), so AI runtime
names carry the production scope format (``[AI_JP:JP] ...``) and region
attribution must work through canonical provider metadata -- hand-written
``[AI:JP]`` fixtures that masked this drift are gone.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

import clash_relay.ai_application as ai_application
from clash_relay.ai_application import (
    _ai_provider_regions,
    _node_regions,
)
from clash_relay.ai_probe_environment import (
    evaluate_endpoint_blockage,
    select_sentinels,
)
from clash_relay.ai_qualification import (
    _new_diagnostics,
    load_ai_probe_specs,
)
from clash_relay.ai_qualification_cache import (
    ai_runtime_fingerprints,
    derive_ai_cache_key,
    qualification_cache_key,
    update_ai_cache_service,
)
from clash_relay.builder import build_candidate
from clash_relay.policy_document import (
    load_policy_document,
    policy_fragment_path,
)
from clash_relay.promotion_guard import assess_promotion, load_promotion_guard_policy
from clash_relay.service_qualification import service_qualification_by_probe

_EPOCH = 1_800_000_000
_ALL_REGIONS = ("JP", "KR", "SG", "TW", "US")
_REGIONS_BY_SOURCE = {
    1: ["Hong Kong", "Taiwan", "Singapore", "Japan", "Korea", "US"],
    2: ["Hong Kong", "Taiwan"],
    3: ["Singapore", "Japan"],
    4: ["Korea"],
    5: ["US"],
}


def _generated_candidate(repo_root: Path, tmp_path: Path):
    """Build one real canonical candidate with nodes in every AI region."""

    urls = {
        f"SUBSCRIPTION_{index}_URL": f"https://fixture.invalid/sub/{index}" for index in range(1, 6)
    }

    def fetcher(url: str, **_kwargs) -> str:
        source = int(url.rsplit("/", 1)[1])
        proxies = [
            {
                "name": f"{region} Node S{source}",
                "type": "http",
                "server": f"{region.lower().replace(' ', '-')}-s{source}.fixture.invalid",
                "port": 20000 + source,
            }
            for region in _REGIONS_BY_SOURCE[source]
        ]
        return yaml.safe_dump({"proxies": proxies}, allow_unicode=True, sort_keys=False)

    def rule_fetcher(_url: str, **_kwargs) -> str:
        return "DOMAIN-SUFFIX,fixture.invalid\n"

    root = tmp_path / "generated"
    root.mkdir(parents=True)
    for name in ("config.yaml", "subscriptions.yaml", "policies.yaml"):
        (root / name).write_text((repo_root / name).read_text(encoding="utf-8"), encoding="utf-8")
    shutil.copytree(repo_root / "policies", root / "policies")
    shutil.copytree(repo_root / "rules", root / "rules")

    return build_candidate(
        config_path=root / "config.yaml",
        subscriptions_path=root / "subscriptions.yaml",
        policies_path=root / "policies.yaml",
        env=urls,
        fetcher=fetcher,
        rule_fetcher=rule_fetcher,
    )


def test_generated_ai_runtime_names_use_production_scope_format(
    repo_root: Path, tmp_path: Path
) -> None:
    """P0 drift guard: production AI names are [AI_JP:JP] ... and the
    canonical provider-metadata region mapping covers every AI node."""

    result = _generated_candidate(repo_root, tmp_path)
    policies_document = load_policy_document(tmp_path / "generated" / "policies.yaml").document
    provider_regions = _ai_provider_regions(policies_document, result.config)
    node_regions = _node_regions(result.config, provider_regions)

    assert set(provider_regions.values()) == set(_ALL_REGIONS)
    for name in node_regions:
        scope = name.split("]")[0]
        assert scope.startswith("[AI_"), name
        assert node_regions[name] in scope
    # Canonical metadata covers every AI node; nothing falls back to other.
    assert "other" not in set(node_regions.values())


def test_select_sentinels_covers_every_region_from_full_inventory() -> None:
    node_regions = {
        "[AI_US:US] sub_5/a": "US",
        "[AI_US:US] sub_5/b": "US",
        "[AI_JP:JP] sub_5/c": "JP",
        "[AI_SG:SG] sub_5/c": "SG",
        "[AI_KR:KR] sub_5/d": "KR",
    }

    sentinels = select_sentinels(node_regions=node_regions)

    assert len(sentinels) == 4
    assert {node_regions[name] for name in sentinels} == {"US", "JP", "SG", "KR"}
    # Deterministic regardless of input iteration order.
    assert sentinels == select_sentinels(node_regions=dict(reversed(list(node_regions.items()))))


def test_endpoint_blockage_requires_two_regions_and_control_coverage() -> None:
    critical = ["ai_openai", "openai_app_android"]
    stats = {
        "jp": {
            "ai_openai": {"probed": 1, "outcomes": {"timeout": 1}},
            "openai_app_android": {"probed": 1, "outcomes": {"status_403": 1}},
        },
        "sg": {
            "ai_openai": {"probed": 1, "outcomes": {"timeout": 1}},
            "openai_app_android": {"probed": 1, "outcomes": {"status_403": 1}},
        },
    }

    # Both critical endpoints network-fail across two control-covered regions.
    verdict = evaluate_endpoint_blockage(
        critical_endpoints=critical,
        region_endpoint_stats=stats,
        control_ok_regions={"jp", "sg"},
    )
    assert verdict["systemic"] is True
    assert verdict["blocked_critical_endpoints"] == ["ai_openai"]
    assert verdict["dominant_failure_category"] == "timeout"

    # One region only: insufficient scope for a systemic verdict.
    single = evaluate_endpoint_blockage(
        critical_endpoints=critical,
        region_endpoint_stats={"jp": stats["jp"]},
        control_ok_regions={"jp"},
    )
    assert single["systemic"] is False

    # Control coverage missing for one failing region: not systemic.
    partial = evaluate_endpoint_blockage(
        critical_endpoints=critical,
        region_endpoint_stats=stats,
        control_ok_regions={"jp"},
    )
    assert partial["systemic"] is False

    # Any HTTP response on the endpoint proves it is reachable.
    reached = evaluate_endpoint_blockage(
        critical_endpoints=critical,
        region_endpoint_stats={
            "jp": {
                "ai_openai": {"probed": 1, "outcomes": {"status_403": 1}},
                "openai_app_android": {"probed": 1, "outcomes": {"status_403": 1}},
            },
            "sg": {
                "ai_openai": {"probed": 1, "outcomes": {"timeout": 1}},
                "openai_app_android": {"probed": 1, "outcomes": {"timeout": 1}},
            },
        },
        control_ok_regions={"jp", "sg"},
    )
    assert reached["systemic"] is False


def _probe_family(probes: tuple[dict[str, Any], ...]) -> str:
    names = {str(probe["name"]) for probe in probes}
    if "ai_openai" in names or any(name.startswith("openai_app_") for name in names):
        return "openai"
    if any(name.startswith("openai_support_") for name in names):
        return "support"
    if "connectivity" in names:
        return "connectivity"
    if "ai_claude" in names:
        return "claude"
    if "ai_gemini" in names:
        return "gemini"
    raise AssertionError(f"unexpected probe set {names}")


def _patch_probe_environment(
    monkeypatch: pytest.MonkeyPatch,
    *,
    candidate_config: dict[str, Any],
    openai_outcome_by_region: dict[str, str],
    connectivity_outcome_by_region: dict[str, str],
    claude_ok: bool = True,
    gemini_ok: bool = True,
) -> list[set[str]]:
    """Stub _probe_names with region-scoped outcomes per probe family.

    Regions without an entry in the openai/connectivity maps are *skipped*
    (never probed), which models partial connectivity coverage.
    """

    openai_calls: list[set[str]] = []
    openai_outcome_by_region = dict(openai_outcome_by_region)
    connectivity_outcome_by_region = dict(connectivity_outcome_by_region)

    def probe(
        *,
        binary: Path,
        candidate: Path,
        names: set[str] | None,
        probes: tuple[dict[str, Any], ...],
        workers: int,
        diagnostics: dict[str, Any] | None = None,
        provider_regions: dict[str, str] | None = None,
    ) -> tuple[set[str], dict[str, Any]]:
        family = _probe_family(probes)
        document_providers = yaml.safe_load(Path(candidate).read_text(encoding="utf-8"))[
            "proxy-providers"
        ]
        regions_for_nodes: dict[str, str] = {}
        if names is None:
            # Real semantics: names=None probes every AI-provider node only.
            for provider_name, provider in document_providers.items():
                if not str(provider_name).startswith("cr_ai_"):
                    continue
                for row in provider.get("payload") or []:
                    if isinstance(row, dict) and isinstance(row.get("name"), str):
                        regions_for_nodes[row["name"]] = (provider_regions or {}).get(
                            provider_name, "other"
                        )
        else:
            for name in names:
                for provider_name, provider in document_providers.items():
                    payload_names = {
                        str(row.get("name"))
                        for row in (provider.get("payload") or [])
                        if isinstance(row, dict)
                    }
                    if name in payload_names:
                        regions_for_nodes[name] = (provider_regions or {}).get(
                            provider_name, "other"
                        )

        document = _new_diagnostics(probes)
        document["tested_nodes"] = len(regions_for_nodes)
        qualified: set[str] = set()
        for name in sorted(regions_for_nodes):
            region = regions_for_nodes[name]
            row = document.setdefault("regions", {}).setdefault(
                region, {"tested": 0, "qualified": 0, "endpoints": {}}
            )
            row["tested"] += 1
            node_ok = True
            for probe_spec in probes:
                endpoint = str(probe_spec["name"])
                if family == "openai":
                    outcome = openai_outcome_by_region.get(region, "timeout")
                    passed = outcome.startswith("status_") and outcome != "status_403"
                elif family == "connectivity":
                    outcome = connectivity_outcome_by_region.get(region, "timeout")
                    passed = outcome == "status_204"
                elif family == "claude":
                    outcome = "status_200" if claude_ok else "timeout"
                    passed = claude_ok
                elif family == "support":
                    outcome = "status_200"
                    passed = True
                else:
                    outcome = "status_200" if gemini_ok else "timeout"
                    passed = gemini_ok
                stats = row["endpoints"].setdefault(endpoint, {"probed": 0, "outcomes": {}})
                stats["probed"] += 1
                stats["outcomes"][outcome] = int(stats["outcomes"].get(outcome, 0)) + 1
                node_ok = node_ok and passed
            if node_ok:
                row["qualified"] += 1
                document["qualified_nodes"] += 1
                qualified.add(name)
            if family == "openai":
                openai_calls.append({name})
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics.update(document)
        return qualified, document

    monkeypatch.setattr(ai_application, "_probe_names", probe)
    return openai_calls


def _patch_rewrite(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]) -> None:
    def rewrite(candidate_path: Path, qualified_by_probe: dict[str, set[str]], **kwargs):
        captured["qualified_by_probe"] = {
            service: set(names) for service, names in qualified_by_probe.items()
        }
        return {
            "qualification_mode": "per-service",
            "tested_nodes": 12,
            "qualified_nodes": sum(len(names) for names in qualified_by_probe.values()),
            "country_groups": {},
            "removed_country_groups": [],
            "service_qualified_nodes": {
                service: len(names) for service, names in qualified_by_probe.items()
            },
            "service_country_groups": {},
            "service_fail_closed": [],
            "service_rules": {},
            "preferred_regions": [],
        }

    monkeypatch.setattr(ai_application, "rewrite_ai_service_qualified_candidate", rewrite)
    monkeypatch.setattr(
        ai_application,
        "apply_service_route_postprocessing",
        lambda candidate_path: {"route_postprocess": {}},
    )


def test_openai_systemic_blackout_is_inconclusive_on_generated_candidate(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every region's OpenAI probes time out while connectivity succeeds
    through the same sentinels: the evidence is inconclusive, the full sweep
    is skipped, and the openai runtime is held on its full unverified pool."""

    built = _generated_candidate(repo_root, tmp_path)
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text(built.yaml_text, encoding="utf-8")

    blackout = dict.fromkeys(_ALL_REGIONS, "timeout")
    control_ok = dict.fromkeys(_ALL_REGIONS, "status_204")
    openai_calls = _patch_probe_environment(
        monkeypatch,
        candidate_config=built.config,
        openai_outcome_by_region=blackout,
        connectivity_outcome_by_region=control_ok,
    )
    captured: dict[str, Any] = {}
    _patch_rewrite(monkeypatch, captured)

    result = ai_application.run_ai_qualification(
        candidate=candidate,
        policies=repo_root / "policies.yaml",
        mihomo_bin=tmp_path / "mihomo",
    )

    evidence = result["service_evidence"]["openai"]
    assert evidence["evidence_status"] == "inconclusive"
    assert evidence["systemic_failure_detected"] is True
    assert evidence["systemic_trigger"] == "sentinel_gate"
    assert evidence["blocked_critical_endpoints"] == [
        "ai_openai",
        "openai_app_android",
        "openai_app_auth",
        "openai_app_setup_auth",
    ]
    assert evidence["dominant_failure_category"] == "timeout"
    assert sorted(evidence["sentinel_regions"]) == ["JP", "KR", "SG", "TW", "US"]
    assert evidence["inconclusive"] >= 5
    assert evidence["live_failed"] == 0
    assert evidence["evidence_source"] == "none"
    assert evidence["lkg_fresh"] is False
    # Only the bounded sentinel sweep touched OpenAI.
    assert all(len(names) <= len(evidence["sentinel_regions"]) for names in openai_calls)
    # HOLD routing keeps the full unverified AI pool (never REJECT).
    assert len(captured["qualified_by_probe"]["ai_openai"]) == 5
    # Claude and Gemini keep their independent live qualification.
    assert result["service_evidence"]["claude"]["evidence_status"] == "passed"
    assert result["service_evidence"]["gemini"]["evidence_status"] == "passed"
    assert result["service_evidence"]["openai"]["control_ok_regions"] == [
        "JP",
        "KR",
        "SG",
        "TW",
        "US",
    ]


def test_systemic_failure_never_writes_negative_cache(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = _generated_candidate(repo_root, tmp_path)
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text(built.yaml_text, encoding="utf-8")
    key = derive_ai_cache_key("token")
    fingerprints = ai_runtime_fingerprints(built.config, key)
    # Seed claude passes for every node; openai has no records at all.
    seeded = update_ai_cache_service(
        {"version": 1, "nodes": {}},
        fingerprints,
        "ai_claude",
        checked_names=set(fingerprints),
        passed_names=set(fingerprints),
        now_epoch=_EPOCH,
    )
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps(seeded), encoding="utf-8")
    cache_key = tmp_path / "cache.key"
    cache_key.write_text(key.hex(), encoding="ascii")

    blackout = dict.fromkeys(_ALL_REGIONS, "timeout")
    control_ok = dict.fromkeys(_ALL_REGIONS, "status_204")
    _patch_probe_environment(
        monkeypatch,
        candidate_config=built.config,
        openai_outcome_by_region=blackout,
        connectivity_outcome_by_region=control_ok,
    )
    captured: dict[str, Any] = {}
    _patch_rewrite(monkeypatch, captured)
    monkeypatch.setattr("clash_relay.ai_qualification_cache.time.time", lambda: _EPOCH + 60)

    result = ai_application.run_ai_qualification(
        candidate=candidate,
        policies=repo_root / "policies.yaml",
        mihomo_bin=tmp_path / "mihomo",
        cache=cache,
        cache_key=cache_key,
        next_cache=tmp_path / "next-cache.json",
    )

    assert result["service_evidence"]["openai"]["evidence_status"] == "inconclusive"
    written = json.loads((tmp_path / "next-cache.json").read_text(encoding="utf-8"))
    claude_epochs = set()
    for record in written["nodes"].values():
        for service, entry in record["services"].items():
            if service.startswith("ai_openai"):
                raise AssertionError(f"openai cache record written under systemic failure: {entry}")
            if service == "ai_claude":
                claude_epochs.add(entry["checked_epoch"])
    # Pre-existing claude passes keep their original checked_epoch.
    assert claude_epochs == {_EPOCH}


def test_fresh_openai_pass_cache_provides_bounded_lkg(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = _generated_candidate(repo_root, tmp_path)
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text(built.yaml_text, encoding="utf-8")
    key = derive_ai_cache_key("token")
    fingerprints = ai_runtime_fingerprints(built.config, key)
    primary = load_ai_probe_specs(
        policy_fragment_path(repo_root / "policies.yaml", "scheduling"),
        names=("ai_openai",),
    )[0]
    probes = service_qualification_by_probe("ai_openai").qualification_probes(primary)
    from clash_relay.openai_app_contract import cache_service_key

    service_key = qualification_cache_key(cache_service_key("ai_openai"), probes)
    node_regions = _node_regions(
        built.config,
        _ai_provider_regions(
            load_policy_document(tmp_path / "generated" / "policies.yaml").document,
            built.config,
        ),
    )
    lkg_name = next(name for name, region in node_regions.items() if region == "JP")
    cache_document = {"version": 1, "nodes": {}}
    cache_document["nodes"].setdefault(fingerprints[lkg_name], {"services": {}})["services"][
        service_key
    ] = {"passed": True, "checked_epoch": _EPOCH}
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps(cache_document), encoding="utf-8")
    cache_key = tmp_path / "cache.key"
    cache_key.write_text(key.hex(), encoding="ascii")

    blackout = dict.fromkeys(_ALL_REGIONS, "timeout")
    control_ok = dict.fromkeys(_ALL_REGIONS, "status_204")
    _patch_probe_environment(
        monkeypatch,
        candidate_config=built.config,
        openai_outcome_by_region=blackout,
        connectivity_outcome_by_region=control_ok,
    )
    captured: dict[str, Any] = {}
    _patch_rewrite(monkeypatch, captured)
    monkeypatch.setattr("clash_relay.ai_qualification_cache.time.time", lambda: _EPOCH + 60)

    result = ai_application.run_ai_qualification(
        candidate=candidate,
        policies=repo_root / "policies.yaml",
        mihomo_bin=tmp_path / "mihomo",
        cache=cache,
        cache_key=cache_key,
        next_cache=tmp_path / "next-cache.json",
    )

    evidence = result["service_evidence"]["openai"]
    assert evidence["evidence_status"] == "inconclusive"
    assert evidence["evidence_source"] == "cache"
    assert evidence["lkg_fresh"] is True
    assert evidence["cache_pass_hits"] == 1
    # LKG routing: the openai service keeps only the cached-pass node.
    assert captured["qualified_by_probe"]["ai_openai"] == {lkg_name}


def test_stale_openai_pass_cache_cannot_pose_as_passed(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = _generated_candidate(repo_root, tmp_path)
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text(built.yaml_text, encoding="utf-8")
    key = derive_ai_cache_key("token")
    fingerprints = ai_runtime_fingerprints(built.config, key)
    primary = load_ai_probe_specs(
        policy_fragment_path(repo_root / "policies.yaml", "scheduling"),
        names=("ai_openai",),
    )[0]
    probes = service_qualification_by_probe("ai_openai").qualification_probes(primary)
    from clash_relay.openai_app_contract import cache_service_key

    service_key = qualification_cache_key(cache_service_key("ai_openai"), probes)
    node_regions = _node_regions(
        built.config,
        _ai_provider_regions(
            load_policy_document(tmp_path / "generated" / "policies.yaml").document,
            built.config,
        ),
    )
    cache_document = {"version": 1, "nodes": {}}
    for name in node_regions:
        cache_document["nodes"].setdefault(fingerprints[name], {"services": {}})["services"][
            service_key
        ] = {"passed": True, "checked_epoch": _EPOCH}
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps(cache_document), encoding="utf-8")
    cache_key = tmp_path / "cache.key"
    cache_key.write_text(key.hex(), encoding="ascii")

    blackout = dict.fromkeys(set(node_regions.values()), "timeout")
    control_ok = dict.fromkeys(set(node_regions.values()), "status_204")
    _patch_probe_environment(
        monkeypatch,
        candidate_config=built.config,
        openai_outcome_by_region=blackout,
        connectivity_outcome_by_region=control_ok,
    )
    captured: dict[str, Any] = {}
    _patch_rewrite(monkeypatch, captured)
    # OpenAI pass TTL is 7200s: an 8-hour-old pass is stale and must not count.
    monkeypatch.setattr("clash_relay.ai_qualification_cache.time.time", lambda: _EPOCH + 8 * 3600)

    result = ai_application.run_ai_qualification(
        candidate=candidate,
        policies=repo_root / "policies.yaml",
        mihomo_bin=tmp_path / "mihomo",
        cache=cache,
        cache_key=cache_key,
        next_cache=tmp_path / "next-cache.json",
    )

    evidence = result["service_evidence"]["openai"]
    assert evidence["evidence_status"] == "inconclusive"
    assert evidence["lkg_fresh"] is False
    assert evidence["cache_pass_hits"] == 0
    assert evidence["evidence_source"] == "none"
    # HOLD routing: the full unverified pool stays in place, never REJECT.
    assert len(captured["qualified_by_probe"]["ai_openai"]) == len(node_regions)


def test_partial_control_success_is_not_misjudged_as_systemic(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control succeeds in one region only: the multi-region OpenAI failure
    cannot be declared systemic and is recorded as confirmed node failure."""

    built = _generated_candidate(repo_root, tmp_path)
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text(built.yaml_text, encoding="utf-8")
    key = derive_ai_cache_key("token")
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"version": 1, "nodes": {}}), encoding="utf-8")
    cache_key = tmp_path / "cache.key"
    cache_key.write_text(key.hex(), encoding="ascii")

    blackout = dict.fromkeys(_ALL_REGIONS, "timeout")
    control_partial = dict.fromkeys(_ALL_REGIONS, "timeout")
    control_partial["JP"] = "status_204"
    _patch_probe_environment(
        monkeypatch,
        candidate_config=built.config,
        openai_outcome_by_region=blackout,
        connectivity_outcome_by_region=control_partial,
    )
    captured: dict[str, Any] = {}
    _patch_rewrite(monkeypatch, captured)
    monkeypatch.setattr("clash_relay.ai_qualification_cache.time.time", lambda: _EPOCH + 60)

    result = ai_application.run_ai_qualification(
        candidate=candidate,
        policies=repo_root / "policies.yaml",
        mihomo_bin=tmp_path / "mihomo",
        cache=cache,
        cache_key=cache_key,
        next_cache=tmp_path / "next-cache.json",
    )

    evidence = result["service_evidence"]["openai"]
    assert evidence["evidence_status"] == "failed"
    assert evidence["systemic_failure_detected"] is False
    written = json.loads((tmp_path / "next-cache.json").read_text(encoding="utf-8"))
    openai_fails = sum(
        1
        for record in written["nodes"].values()
        for service, entry in record["services"].items()
        if service.startswith("ai_openai") and not entry["passed"]
    )
    assert openai_fails == 5


def test_openai_recovery_reenters_live_qualification(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a systemic blackout (which writes nothing), the next healthy run
    re-qualifies every node live."""

    built = _generated_candidate(repo_root, tmp_path)
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text(built.yaml_text, encoding="utf-8")
    key = derive_ai_cache_key("token")
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"version": 1, "nodes": {}}), encoding="utf-8")
    cache_key = tmp_path / "cache.key"
    cache_key.write_text(key.hex(), encoding="ascii")

    blackout = dict.fromkeys(_ALL_REGIONS, "timeout")
    control_ok = dict.fromkeys(_ALL_REGIONS, "status_204")
    _patch_probe_environment(
        monkeypatch,
        candidate_config=built.config,
        openai_outcome_by_region=blackout,
        connectivity_outcome_by_region=control_ok,
    )
    captured: dict[str, Any] = {}
    _patch_rewrite(monkeypatch, captured)
    monkeypatch.setattr("clash_relay.ai_qualification_cache.time.time", lambda: _EPOCH + 60)

    ai_application.run_ai_qualification(
        candidate=candidate,
        policies=repo_root / "policies.yaml",
        mihomo_bin=tmp_path / "mihomo",
        cache=cache,
        cache_key=cache_key,
        next_cache=tmp_path / "next-cache.json",
    )
    before = json.loads((tmp_path / "next-cache.json").read_text(encoding="utf-8"))
    for record in before["nodes"].values():
        assert not any(service.startswith("ai_openai") for service in record["services"])

    # Recovery: OpenAI probes now pass through every node.
    healthy = dict.fromkeys(_ALL_REGIONS, "status_200")
    _patch_probe_environment(
        monkeypatch,
        candidate_config=built.config,
        openai_outcome_by_region=healthy,
        connectivity_outcome_by_region=control_ok,
    )
    monkeypatch.setattr("clash_relay.ai_qualification_cache.time.time", lambda: _EPOCH + 3600)
    result = ai_application.run_ai_qualification(
        candidate=candidate,
        policies=repo_root / "policies.yaml",
        mihomo_bin=tmp_path / "mihomo",
        cache=cache,
        cache_key=cache_key,
        next_cache=tmp_path / "next-cache.json",
    )

    evidence = result["service_evidence"]["openai"]
    assert evidence["evidence_status"] == "passed"
    assert evidence["systemic_failure_detected"] is False
    assert evidence["evidence_source"] == "live"
    written = json.loads((tmp_path / "next-cache.json").read_text(encoding="utf-8"))
    openai_passes = sum(
        1
        for record in written["nodes"].values()
        for service, entry in record["services"].items()
        if service.startswith("ai_openai") and entry["passed"]
    )
    assert openai_passes == 5


def test_service_evidence_is_privacy_safe(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = _generated_candidate(repo_root, tmp_path)
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text(built.yaml_text, encoding="utf-8")

    blackout = dict.fromkeys(_ALL_REGIONS, "timeout")
    control_ok = dict.fromkeys(_ALL_REGIONS, "status_204")
    _patch_probe_environment(
        monkeypatch,
        candidate_config=built.config,
        openai_outcome_by_region=blackout,
        connectivity_outcome_by_region=control_ok,
    )
    captured: dict[str, Any] = {}
    _patch_rewrite(monkeypatch, captured)

    result = ai_application.run_ai_qualification(
        candidate=candidate,
        policies=repo_root / "policies.yaml",
        mihomo_bin=tmp_path / "mihomo",
    )

    serialized = json.dumps(result["service_evidence"])
    for forbidden in (
        "fixture.invalid",
        "sub_",
        "[AI_",
        "node-",
        "https://",
        "Japan",
        "Singapore",
    ):
        assert forbidden not in serialized


def _canonical_service_policy() -> Any:
    """Canonical service thresholds; fixture uses replace to drop browsing."""

    from dataclasses import replace

    policy = load_promotion_guard_policy(
        Path(__file__).resolve().parents[1] / "promotion-guard.yaml"
    )
    return replace(
        policy,
        minimum_source_ratio_by_use={"general": 0.5},
        minimum_sources_by_use={"general": 1, "ai": 1},
        minimum_nodes_by_use={"general": 1, "ai": 1},
        minimum_regions_by_use={"general": 1, "ai": 1},
    )


def test_promotion_guard_inconclusive_without_lkg_holds_release(
    built_candidate, project_paths
) -> None:
    from clash_relay.config_loader import load_project

    project = load_project(**project_paths)
    policy = _canonical_service_policy()
    qualification = {
        "ai": {
            "service_evidence": {
                "openai": {
                    "evidence_status": "inconclusive",
                    "systemic_failure_detected": True,
                    "lkg_fresh": False,
                    "evidence_source": "none",
                }
            },
            "services": {
                "openai": {"qualified_candidates": 0, "qualified_regions": 0},
                "claude": {"qualified_candidates": 6, "qualified_regions": 1},
                "gemini": {"qualified_candidates": 115, "qualified_regions": 3},
            },
        }
    }

    report = assess_promotion(
        project, built_candidate.config, None, policy, qualification=qualification
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "probe_environment_hold"
    assert "probe_environment_hold:openai" in report["violations"]
    # The hold must not masquerade as a confirmed node-failure violation.
    assert "minimum_qualified_nodes:openai" not in report["violations"]
    assert report["probe_environment"]["held_services"] == ["openai"]


def test_promotion_guard_inconclusive_with_lkg_continues(built_candidate, project_paths) -> None:
    from clash_relay.config_loader import load_project

    project = load_project(**project_paths)
    policy = _canonical_service_policy()
    qualification = {
        "ai": {
            "service_evidence": {
                "openai": {
                    "evidence_status": "inconclusive",
                    "systemic_failure_detected": True,
                    "lkg_fresh": True,
                    "evidence_source": "cache",
                }
            },
            "services": {
                "openai": {"qualified_candidates": 2, "qualified_regions": 1},
                "claude": {"qualified_candidates": 6, "qualified_regions": 1},
                "gemini": {"qualified_candidates": 115, "qualified_regions": 3},
            },
        }
    }

    report = assess_promotion(
        project, built_candidate.config, None, policy, qualification=qualification
    )

    assert report["status"] == "passed"
    assert report["probe_environment"]["cache_backed_services"] == ["openai"]
    assert report["probe_environment"]["held_services"] == []


def test_promotion_guard_confirmed_failure_blocks_as_before(built_candidate, project_paths) -> None:
    from clash_relay.config_loader import load_project

    project = load_project(**project_paths)
    policy = _canonical_service_policy()
    qualification = {
        "ai": {
            "service_evidence": {
                "openai": {
                    "evidence_status": "failed",
                    "systemic_failure_detected": False,
                    "lkg_fresh": False,
                    "evidence_source": "live",
                }
            },
            "services": {
                "openai": {"qualified_candidates": 0, "qualified_regions": 0},
                "claude": {"qualified_candidates": 6, "qualified_regions": 1},
                "gemini": {"qualified_candidates": 115, "qualified_regions": 3},
            },
        }
    }

    report = assess_promotion(
        project,
        built_candidate.config,
        built_candidate.config,
        policy,
        qualification=qualification,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "degraded"
    assert "minimum_qualified_nodes:openai" in report["violations"]
    assert "minimum_qualified_regions:openai" in report["violations"]
    assert report["probe_environment"]["held_services"] == []
