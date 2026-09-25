"""Regression tests for systemic OpenAI probe-environment isolation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import clash_relay.ai_application as ai_application
from clash_relay.ai_probe_environment import (
    detect_systemic_failure,
    extract_region,
    select_sentinels,
)
from clash_relay.ai_qualification import _new_diagnostics
from clash_relay.ai_qualification_cache import (
    derive_ai_cache_key,
    update_ai_cache_service,
)
from clash_relay.promotion_guard import assess_promotion
from clash_relay.util import dump_yaml

_EPOCH = 1_800_000_000


def _candidate_config() -> dict:
    providers = {}
    for region in ("jp", "sg", "us"):
        providers[f"cr_ai_{region}_{region}"] = {
            "type": "inline",
            "payload": [
                {
                    "name": f"[AI:{region.upper()}] sub_5/node-{region}-{index}",
                    "type": "http",
                    "server": f"{index}.{region}.invalid.example",
                    "port": 443,
                }
                for index in range(1, 5)
            ],
        }
    return {"proxy-providers": providers}


def _candidate_file(tmp_path: Path) -> Path:
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text(dump_yaml(_candidate_config()), encoding="utf-8")
    return candidate


def _policies_file(repo_root: Path) -> Path:
    """The canonical V2 policy manifest (scheduling carries the control probe)."""

    return repo_root / "policies.yaml"


def _diag(
    *,
    probe_name: str,
    tested_by_region: dict[str, int],
    passed_by_region: dict[str, int],
    outcomes: dict[str, int],
) -> dict:
    document = _new_diagnostics(({"name": probe_name, "method": "HEAD", "expected_status": "204"},))
    for region, tested in tested_by_region.items():
        row = document.setdefault("regions", {}).setdefault(region, {"tested": 0, "qualified": 0})
        row["tested"] += tested
        row["qualified"] += passed_by_region.get(region, 0)
    summary = document["probes"][probe_name]
    summary["passed"] = sum(passed_by_region.values())
    summary["failed"] = document["tested_nodes"] = (
        sum(tested_by_region.values()) - summary["passed"]
    )
    summary["outcomes"] = dict(outcomes)
    return document


def test_sentinel_selection_is_deterministic_and_spans_regions() -> None:
    names = {
        "[AI:US] sub_5/node-b",
        "[AI:US] sub_5/node-a",
        "[AI:JP] sub_5/node-c",
        "[AI:SG] sub_5/node-d",
        "[AI:JP] sub_5/node-e",
    }

    first = select_sentinels(names)
    second = select_sentinels(names)

    assert first == second
    assert len(first) == 3
    regions = {extract_region(name) for name in first}
    assert regions == {"us", "jp", "sg"}


def test_sentinel_selection_is_bounded_for_single_region() -> None:
    names = {f"[AI:JP] sub_5/node-{index}" for index in range(10)}

    sentinels = select_sentinels(names, count=3)

    assert len(sentinels) == 3
    assert {extract_region(name) for name in sentinels} == {"jp"}


def test_detect_systemic_failure_requires_all_conditions() -> None:
    outcomes = {"timeout": 6, "status_403": 1}

    assert detect_systemic_failure(
        live_tested=7,
        qualified_nodes=0,
        tested_regions=3,
        failed_regions=3,
        outcome_counts=outcomes,
        control_passed=True,
    )[0]
    # A qualifying node means the environment works.
    assert not detect_systemic_failure(
        live_tested=7,
        qualified_nodes=1,
        tested_regions=3,
        failed_regions=3,
        outcome_counts=outcomes,
        control_passed=True,
    )[0]
    # HTTP rejections prove the probe reached OpenAI: genuine failures.
    assert not detect_systemic_failure(
        live_tested=7,
        qualified_nodes=0,
        tested_regions=3,
        failed_regions=3,
        outcome_counts={"status_403": 7},
        control_passed=True,
    )[0]
    # A failed connectivity control means the environment cannot be judged.
    assert not detect_systemic_failure(
        live_tested=7,
        qualified_nodes=0,
        tested_regions=3,
        failed_regions=3,
        outcome_counts=outcomes,
        control_passed=False,
    )[0]
    # A single region cannot evidence a systemic environment failure.
    assert not detect_systemic_failure(
        live_tested=7,
        qualified_nodes=0,
        tested_regions=1,
        failed_regions=1,
        outcome_counts=outcomes,
        control_passed=True,
    )[0]


def _patch_probe_environment(
    monkeypatch: pytest.MonkeyPatch,
    *,
    openai_outcomes: dict[str, int],
    connectivity_passes: bool,
    claude_passes: bool,
    gemini_passes: bool,
    openai_passes: bool = False,
) -> dict[str, int]:
    """Stub _probe_names per probe family; return openai live probe calls."""

    openai_calls: list[set[str]] = []

    def probe(
        *,
        binary: Path,
        candidate: Path,
        names: set[str] | None,
        probes: tuple[dict[str, Any], ...],
        workers: int,
        diagnostics: dict[str, Any] | None = None,
    ) -> set[str]:
        probe_names = [str(probe["name"]) for probe in probes]
        if "ai_openai" in probe_names:
            openai_calls.append(set(names or ()))
            tested = len(names) if names is not None else 12
            passed = tested if openai_passes else 0
            document = _diag(
                probe_name="ai_openai",
                tested_by_region={
                    "jp": tested // 3,
                    "sg": tested // 3,
                    "us": tested - 2 * (tested // 3),
                },
                passed_by_region=dict.fromkeys(
                    ("jp", "sg", "us"), tested // 3 if openai_passes else 0
                ),
                outcomes={} if openai_passes else dict(openai_outcomes),
            )
            document["probes"]["openai_app_android"] = {
                "passed": passed,
                "failed": tested - passed,
                "outcomes": {} if openai_passes else dict(openai_outcomes),
            }
            document["probes"]["openai_app_auth"] = {
                "passed": passed,
                "failed": tested - passed,
                "outcomes": {} if openai_passes else dict(openai_outcomes),
            }
            if diagnostics is not None:
                diagnostics.clear()
                diagnostics.update(document)
            if openai_passes:
                # Every probed node passes when the environment is healthy.
                return (set(names or set()), document)
            return (set(), document)
        if "connectivity" in probe_names:
            document = _diag(
                probe_name="connectivity",
                tested_by_region={"jp": 1, "sg": 1, "us": 1},
                passed_by_region={"jp": 1, "sg": 1, "us": 1} if connectivity_passes else {},
                outcomes={} if connectivity_passes else {"timeout": 3},
            )
            qualified = (
                {f"[AI:{r.upper()}] sub_5/x" for r in ("jp", "sg", "us")}
                if connectivity_passes
                else set()
            )
            if diagnostics is not None:
                diagnostics.clear()
                diagnostics.update(document)
            return (qualified, document)
        if "ai_claude" in probe_names:
            document = _diag(
                probe_name="ai_claude",
                tested_by_region={"jp": 1},
                passed_by_region={"jp": 1} if claude_passes else {},
                outcomes={} if claude_passes else {"timeout": 1},
            )
            qualified = {"[AI:JP] sub_5/node-jp-1"} if claude_passes else set()
            if diagnostics is not None:
                diagnostics.clear()
                diagnostics.update(document)
            return (qualified, document)
        if "ai_gemini" in probe_names:
            document = _diag(
                probe_name="ai_gemini",
                tested_by_region={"jp": 1},
                passed_by_region={"jp": 1} if gemini_passes else {},
                outcomes={} if gemini_passes else {"timeout": 1},
            )
            qualified = {"[AI:JP] sub_5/node-jp-1"} if gemini_passes else set()
            if diagnostics is not None:
                diagnostics.clear()
                diagnostics.update(document)
            return (qualified, document)
        if any(name.startswith("openai_support_") for name in probe_names):
            # Supporting endpoints are diagnostics-only and never gate routing.
            document = _diag(
                probe_name=probe_names[0],
                tested_by_region={"jp": 1},
                passed_by_region={"jp": 1},
                outcomes={},
            )
            if diagnostics is not None:
                diagnostics.clear()
                diagnostics.update(document)
            return ({"[AI:JP] sub_5/node-jp-1"}, document)
        raise AssertionError(f"unexpected probe set {probe_names}")

    monkeypatch.setattr(ai_application, "_probe_names", probe)
    return openai_calls


def _patch_rewrite(monkeypatch: pytest.MonkeyPatch, captured: dict) -> None:
    def rewrite(candidate: Path, qualified_by_probe: dict[str, set[str]], **kwargs):
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
        lambda candidate: {"route_postprocess": {}},
    )


def test_openai_systemic_blackout_is_inconclusive_and_skips_full_probe(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_file(tmp_path)
    policies = _policies_file(repo_root)
    openai_calls = _patch_probe_environment(
        monkeypatch,
        openai_outcomes={"timeout": 1},
        connectivity_passes=True,
        claude_passes=True,
        gemini_passes=True,
    )
    captured: dict = {}
    _patch_rewrite(monkeypatch, captured)

    result = ai_application.run_ai_qualification(
        candidate=candidate,
        policies=policies,
        mihomo_bin=tmp_path / "mihomo",
    )

    evidence = result["service_evidence"]["openai"]
    assert evidence["evidence_status"] == "inconclusive"
    assert evidence["systemic_failure_detected"] is True
    assert evidence["dominant_failure_category"] == "timeout"
    assert evidence["inconclusive"] == 3
    assert evidence["live_failed"] == 0
    assert evidence["evidence_source"] == "none"
    # The full sweep is skipped: only the bounded sentinels touched OpenAI.
    assert all(len(names) <= 3 for names in openai_calls)
    # Claude and Gemini keep their independent live qualification.
    assert result["service_evidence"]["claude"]["evidence_status"] == "passed"
    assert result["service_evidence"]["gemini"]["evidence_status"] == "passed"
    # HOLD routing keeps the full unverified pool instead of collapsing to REJECT.
    assert len(captured["qualified_by_probe"]["ai_openai"]) == 12


def test_systemic_failure_never_writes_negative_cache(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_file(tmp_path)
    policies = _policies_file(repo_root)
    key = derive_ai_cache_key("token")
    # Seed a fresh openai pass for one node (LKG) and fresh passes elsewhere.
    fingerprints = {}
    from clash_relay.ai_qualification_cache import ai_runtime_fingerprints

    fingerprints = ai_runtime_fingerprints(_candidate_config(), key)
    seeded = update_ai_cache_service(
        {"version": 1, "nodes": {}},
        fingerprints,
        "ai_claude",
        checked_names=set(fingerprints),
        passed_names=set(fingerprints),
        now_epoch=_EPOCH,
    )
    lkg_name = "[AI:JP] sub_5/node-jp-1"
    lkg_fingerprint = fingerprints[lkg_name]
    seeded["nodes"][lkg_fingerprint]["services"]["ai_openai@seeded"] = {
        "passed": True,
        "checked_epoch": _EPOCH,
    }
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps(seeded), encoding="utf-8")
    cache_key = tmp_path / "cache.key"
    cache_key.write_text(key.hex(), encoding="ascii")

    _patch_probe_environment(
        monkeypatch,
        openai_outcomes={"timeout": 1},
        connectivity_passes=True,
        claude_passes=True,
        gemini_passes=True,
    )
    captured: dict = {}
    _patch_rewrite(monkeypatch, captured)

    result = ai_application.run_ai_qualification(
        candidate=candidate,
        policies=policies,
        mihomo_bin=tmp_path / "mihomo",
        cache=cache,
        cache_key=cache_key,
        next_cache=tmp_path / "next-cache.json",
    )

    evidence = result["service_evidence"]["openai"]
    assert evidence["evidence_status"] == "inconclusive"
    assert evidence["lkg_fresh"] is False  # cache key binds the contract fingerprint
    # The next cache must not contain any negative openai record.
    written = json.loads((tmp_path / "next-cache.json").read_text(encoding="utf-8"))
    for fingerprint, record in written["nodes"].items():
        for service, entry in record["services"].items():
            if service.startswith("ai_openai"):
                assert entry["passed"] is True, f"negative openai cache entry found: {fingerprint}"


def test_fresh_openai_pass_cache_provides_bounded_lkg(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_file(tmp_path)
    policies = _policies_file(repo_root)
    key = derive_ai_cache_key("token")
    from clash_relay.ai_qualification_cache import ai_runtime_fingerprints

    fingerprints = ai_runtime_fingerprints(_candidate_config(), key)
    cache_document = {"version": 1, "nodes": {}}
    lkg_names = ["[AI:JP] sub_5/node-jp-1", "[AI:SG] sub_5/node-sg-1"]
    # The openai cache key binds the App contract fingerprint; derive it the
    # same way the qualification loop does.
    from clash_relay.ai_qualification import load_ai_probe_specs
    from clash_relay.ai_qualification_cache import qualification_cache_key
    from clash_relay.openai_app_contract import cache_service_key
    from clash_relay.policy_document import policy_fragment_path
    from clash_relay.service_qualification import service_qualification_by_probe

    primary = load_ai_probe_specs(
        policy_fragment_path(repo_root / "policies.yaml", "scheduling"),
        names=("ai_openai",),
    )[0]
    probes = service_qualification_by_probe("ai_openai").qualification_probes(primary)
    service_key = qualification_cache_key(cache_service_key("ai_openai"), probes)
    for name in lkg_names:
        fingerprint = fingerprints[name]
        record = cache_document["nodes"].setdefault(fingerprint, {"services": {}})
        record["services"][service_key] = {"passed": True, "checked_epoch": _EPOCH}
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps(cache_document), encoding="utf-8")
    cache_key = tmp_path / "cache.key"
    cache_key.write_text(key.hex(), encoding="ascii")

    _patch_probe_environment(
        monkeypatch,
        openai_outcomes={"timeout": 1},
        connectivity_passes=True,
        claude_passes=True,
        gemini_passes=True,
    )
    captured: dict = {}
    _patch_rewrite(monkeypatch, captured)
    monkeypatch.setattr("clash_relay.ai_qualification_cache.time.time", lambda: _EPOCH + 60)

    result = ai_application.run_ai_qualification(
        candidate=candidate,
        policies=policies,
        mihomo_bin=tmp_path / "mihomo",
        cache=cache,
        cache_key=cache_key,
        next_cache=tmp_path / "next-cache.json",
    )

    evidence = result["service_evidence"]["openai"]
    assert evidence["evidence_status"] == "inconclusive"
    assert evidence["evidence_source"] == "cache"
    assert evidence["lkg_fresh"] is True
    assert evidence["cache_pass_hits"] == 2
    # LKG routing: the openai service keeps only the cached-pass nodes.
    assert captured["qualified_by_probe"]["ai_openai"] == set(lkg_names)


def test_stale_openai_pass_cache_cannot_pose_as_passed(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_file(tmp_path)
    policies = _policies_file(repo_root)
    key = derive_ai_cache_key("token")
    from clash_relay.ai_qualification import load_ai_probe_specs
    from clash_relay.ai_qualification_cache import ai_runtime_fingerprints, qualification_cache_key
    from clash_relay.openai_app_contract import cache_service_key
    from clash_relay.service_qualification import service_qualification_by_probe

    fingerprints = ai_runtime_fingerprints(_candidate_config(), key)
    from clash_relay.policy_document import policy_fragment_path

    primary = load_ai_probe_specs(
        policy_fragment_path(repo_root / "policies.yaml", "scheduling"),
        names=("ai_openai",),
    )[0]
    probes = service_qualification_by_probe("ai_openai").qualification_probes(primary)
    service_key = qualification_cache_key(cache_service_key("ai_openai"), probes)
    cache_document = {"version": 1, "nodes": {}}
    for name in ("[AI:JP] sub_5/node-jp-1", "[AI:SG] sub_5/node-sg-1"):
        fingerprint = fingerprints[name]
        record = cache_document["nodes"].setdefault(fingerprint, {"services": {}})
        record["services"][service_key] = {"passed": True, "checked_epoch": _EPOCH}
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps(cache_document), encoding="utf-8")
    cache_key = tmp_path / "cache.key"
    cache_key.write_text(key.hex(), encoding="ascii")

    _patch_probe_environment(
        monkeypatch,
        openai_outcomes={"timeout": 1},
        connectivity_passes=True,
        claude_passes=True,
        gemini_passes=True,
    )
    captured: dict = {}
    _patch_rewrite(monkeypatch, captured)
    # openai pass TTL is 7200s: an 8-hour-old pass is stale and must not count.
    monkeypatch.setattr("clash_relay.ai_qualification_cache.time.time", lambda: _EPOCH + 8 * 3600)

    result = ai_application.run_ai_qualification(
        candidate=candidate,
        policies=policies,
        mihomo_bin=tmp_path / "mihomo",
        cache=cache,
        cache_key=cache_key,
        next_cache=tmp_path / "next-cache.json",
    )

    evidence = result["service_evidence"]["openai"]
    assert evidence["evidence_status"] == "inconclusive"
    assert evidence["lkg_fresh"] is False
    assert evidence["cache_pass_hits"] == 0
    # HOLD routing: the full unverified pool stays in place, never REJECT.
    assert len(captured["qualified_by_probe"]["ai_openai"]) == 12
    assert result["service_evidence"]["openai"]["evidence_source"] == "none"


def test_single_node_failure_still_qualifies_negatively(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One genuinely failing node among healthy nodes is a confirmed failure,
    not a systemic event, and lands in the failure cache as today."""

    candidate = _candidate_file(tmp_path)
    policies = _policies_file(repo_root)
    key = derive_ai_cache_key("token")
    from clash_relay.ai_qualification import load_ai_probe_specs
    from clash_relay.ai_qualification_cache import ai_runtime_fingerprints, qualification_cache_key
    from clash_relay.openai_app_contract import cache_service_key
    from clash_relay.service_qualification import service_qualification_by_probe

    fingerprints = ai_runtime_fingerprints(_candidate_config(), key)
    from clash_relay.policy_document import policy_fragment_path

    primary = load_ai_probe_specs(
        policy_fragment_path(repo_root / "policies.yaml", "scheduling"),
        names=("ai_openai",),
    )[0]
    probes = service_qualification_by_probe("ai_openai").qualification_probes(primary)
    service_key = qualification_cache_key(cache_service_key("ai_openai"), probes)
    cache_document = {"version": 1, "nodes": {}}
    # The JP sentinel node already passed openai recently: environment is fine.
    healthy = "[AI:JP] sub_5/node-jp-1"
    cache_document["nodes"].setdefault(fingerprints[healthy], {"services": {}})[service_key] = {
        "passed": True,
        "checked_epoch": _EPOCH,
    }
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps(cache_document), encoding="utf-8")
    cache_key = tmp_path / "cache.key"
    cache_key.write_text(key.hex(), encoding="ascii")

    def probe(*, binary, candidate, names, probes, workers, diagnostics=None):
        probe_names = [str(probe["name"]) for probe in probes]
        tested = len(names) if names is not None else 12
        document = _diag(
            probe_name=probe_names[0],
            tested_by_region={"jp": tested},
            passed_by_region={"jp": tested if probe_names[0] != "ai_openai" else tested - 1},
            outcomes={} if probe_names[0] != "ai_openai" else {"status_403": 1},
        )
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics.update(document)
        if probe_names[0] != "ai_openai":
            qualified = {f"[AI:JP] sub_5/node-jp-{i}" for i in range(1, tested + 1)}
        else:
            # Every openai node passes except the one 403-rejected node.
            qualified = {name for name in (names or set()) if "node-jp-2" not in name}
        return (qualified, document)

    monkeypatch.setattr(ai_application, "_probe_names", probe)
    captured: dict = {}
    _patch_rewrite(monkeypatch, captured)
    monkeypatch.setattr("clash_relay.ai_qualification_cache.time.time", lambda: _EPOCH + 60)

    result = ai_application.run_ai_qualification(
        candidate=candidate,
        policies=policies,
        mihomo_bin=tmp_path / "mihomo",
        cache=cache,
        cache_key=cache_key,
        next_cache=tmp_path / "next-cache.json",
    )

    evidence = result["service_evidence"]["openai"]
    assert evidence["evidence_status"] == "passed"
    assert evidence["systemic_failure_detected"] is False
    written = json.loads((tmp_path / "next-cache.json").read_text(encoding="utf-8"))
    failed_fingerprint = fingerprints["[AI:JP] sub_5/node-jp-2"]
    failed_entry = written["nodes"][failed_fingerprint]["services"][service_key]
    assert failed_entry == {"passed": False, "checked_epoch": _EPOCH + 60}


def test_openai_recovery_reenters_live_qualification(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a systemic blackout (which writes nothing), the next healthy run
    re-qualifies every node live."""

    candidate = _candidate_file(tmp_path)
    policies = _policies_file(repo_root)
    key = derive_ai_cache_key("token")
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"version": 1, "nodes": {}}), encoding="utf-8")
    cache_key = tmp_path / "cache.key"
    cache_key.write_text(key.hex(), encoding="ascii")

    _patch_probe_environment(
        monkeypatch,
        openai_outcomes={"timeout": 1},
        connectivity_passes=True,
        claude_passes=True,
        gemini_passes=True,
    )
    captured: dict = {}
    _patch_rewrite(monkeypatch, captured)

    ai_application.run_ai_qualification(
        candidate=candidate,
        policies=policies,
        mihomo_bin=tmp_path / "mihomo",
        cache=cache,
        cache_key=cache_key,
        next_cache=tmp_path / "next-cache.json",
    )
    before = json.loads((tmp_path / "next-cache.json").read_text(encoding="utf-8"))
    openai_records = [
        services["ai_openai"] if "ai_openai" in services else None
        for services in (record.get("services", {}) for record in before["nodes"].values())
    ]
    assert all(entry is None for entry in openai_records)

    # Recovery: OpenAI probes now pass through every node.
    _patch_probe_environment(
        monkeypatch,
        openai_outcomes={},
        connectivity_passes=True,
        claude_passes=True,
        gemini_passes=True,
        openai_passes=True,
    )
    monkeypatch.setattr("clash_relay.ai_qualification_cache.time.time", lambda: _EPOCH + 120)
    result = ai_application.run_ai_qualification(
        candidate=candidate,
        policies=policies,
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
    openai_passes = 0
    for record in written["nodes"].values():
        for service, entry in record["services"].items():
            if service.startswith("ai_openai") and entry["passed"]:
                openai_passes += 1
    assert openai_passes == 12


def test_service_evidence_is_privacy_safe(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate_file(tmp_path)
    policies = _policies_file(repo_root)
    _patch_probe_environment(
        monkeypatch,
        openai_outcomes={"timeout": 1},
        connectivity_passes=True,
        claude_passes=True,
        gemini_passes=True,
    )
    captured: dict = {}
    _patch_rewrite(monkeypatch, captured)

    result = ai_application.run_ai_qualification(
        candidate=candidate,
        policies=policies,
        mihomo_bin=tmp_path / "mihomo",
    )

    serialized = json.dumps(result["service_evidence"])
    for forbidden in ("invalid.example", "sub_5", "[AI:", "node-", "https://"):
        assert forbidden not in serialized


def test_promotion_guard_confirmed_failure_blocks_as_before(built_candidate, project_paths) -> None:
    from clash_relay.config_loader import load_project

    project = load_project(**project_paths)
    candidate = built_candidate.config
    qualification = {
        "ai": {
            "services": {
                "openai": {"qualified_candidates": 0, "qualified_regions": 0},
                "claude": {"qualified_candidates": 6, "qualified_regions": 1},
                "gemini": {"qualified_candidates": 115, "qualified_regions": 6},
            }
        }
    }

    report = assess_promotion(
        project, candidate, None, _guard_policy(), qualification=qualification
    )

    assert report["status"] == "blocked"
    assert "minimum_qualified_nodes:openai" in report["violations"]
    assert "minimum_qualified_regions:openai" in report["violations"]
    assert report["reason"] == "availability_contract"


def test_promotion_guard_inconclusive_with_lkg_continues(built_candidate, project_paths) -> None:
    from clash_relay.config_loader import load_project

    project = load_project(**project_paths)
    candidate = built_candidate.config
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
                "gemini": {"qualified_candidates": 115, "qualified_regions": 6},
            },
        }
    }

    report = assess_promotion(
        project, candidate, None, _guard_policy(), qualification=qualification
    )

    assert report["status"] == "passed"
    assert report["probe_environment"]["cache_backed_services"] == ["openai"]
    assert report["probe_environment"]["held_services"] == []


def test_promotion_guard_inconclusive_without_lkg_holds_release(
    built_candidate, project_paths
) -> None:
    from clash_relay.config_loader import load_project

    project = load_project(**project_paths)
    candidate = built_candidate.config
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
                "gemini": {"qualified_candidates": 115, "qualified_regions": 6},
            },
        }
    }

    report = assess_promotion(
        project, candidate, None, _guard_policy(), qualification=qualification
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "probe_environment_hold"
    assert "probe_environment_hold:openai" in report["violations"]
    # The hold must not masquerade as a confirmed node-failure violation.
    assert "minimum_qualified_nodes:openai" not in report["violations"]
    assert report["probe_environment"]["held_services"] == ["openai"]


def _guard_policy():
    """Canonical service thresholds; use-level minimums scoped to the fixture."""

    from dataclasses import replace

    from clash_relay.promotion_guard import load_promotion_guard_policy

    policy = load_promotion_guard_policy(
        Path(__file__).resolve().parents[1] / "promotion-guard.yaml"
    )
    return replace(
        policy,
        minimum_source_ratio_by_use={},
        minimum_sources_by_use={"general": 1, "ai": 1},
        minimum_nodes_by_use={"general": 1, "ai": 1},
        minimum_regions_by_use={"general": 1, "ai": 1},
    )
