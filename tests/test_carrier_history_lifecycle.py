from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import clash_relay.production_lifecycle as lifecycle
from clash_relay.carrier_collector import collect_carrier_probes
from clash_relay.carrier_observation_receipt import (
    RECEIPT_FILENAME,
    build_carrier_observation_receipt,
    commit_carrier_observation_receipt,
    parse_carrier_observation_receipt,
)
from clash_relay.carrier_probe import build_carrier_probe_payload, sample_probe_targets
from clash_relay.errors import ValidationError
from clash_relay.production_lifecycle import ProductionLifecyclePaths, ProductionPipeline
from clash_relay.production_preflight import ProductionPreflightPipeline

KEY = b"carrier-history-repository-key-123456789"
REPOSITORY = "hzoonp/clash-relay"
SHA = "b" * 40


def _candidate() -> dict:
    payload = []
    for region, source, offset in (("JP", "sub_1", 0), ("HK", "sub_2", 10)):
        for index in range(3):
            payload.append(
                {
                    "name": f"[BROWSING:{region}] {source}/fixture-{index} #0123456789",
                    "type": "http",
                    "server": f"private-{region}-{index}.fixture.invalid",
                    "port": 20000 + offset + index,
                    "password": "private-password",
                }
            )
    return {
        "proxy-providers": {"cr_browsing": {"payload": payload}},
        "proxy-groups": [],
        "rule-providers": {},
        "rules": [],
    }


def _aggregate(candidate: dict, now: int) -> dict:
    sample = sample_probe_targets(candidate=candidate, key=KEY, repository=REPOSITORY)
    outcomes = {
        target.identity: {"reachable": True, "latency_ms": 25.0, "outcome": "tcp_connected"}
        for target in sample.targets
    }
    return collect_carrier_probes(
        [
            build_carrier_probe_payload(
                carrier=carrier, sample=sample, outcomes=outcomes, collected_at_epoch=now
            )
            for carrier in ("telecom", "unicom", "mobile")
        ],
        now_epoch=now,
    )


def _bind_env(monkeypatch) -> None:
    monkeypatch.setenv("CLASH_RELAY_CARRIER_HMAC_KEY", KEY.decode())
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)


def _staged_pipeline(tmp_path: Path, payload: dict) -> ProductionPreflightPipeline:
    source = tmp_path / "carrier-input.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    pipeline = ProductionPreflightPipeline(
        ProductionLifecyclePaths.canonical(tmp_path, carrier_qualification_input=source)
    )
    pipeline._prepare_dirs()
    pipeline._stage_carrier_input()
    return pipeline


def _generated(candidate: dict) -> SimpleNamespace:
    return SimpleNamespace(config=candidate, yaml_text=yaml.safe_dump(candidate), report={})


def test_preflight_issues_read_only_receipt_and_never_persists_history(
    tmp_path: Path, monkeypatch
) -> None:
    now = int(time.time())
    pipeline = _staged_pipeline(tmp_path, _aggregate(_candidate(), now))
    _bind_env(monkeypatch)
    monkeypatch.setenv("GITHUB_SHA", SHA)
    monkeypatch.setattr(
        "clash_relay.production_lifecycle.build_candidate", lambda **_kw: _generated(_candidate())
    )
    assert pipeline._generate()["status"] == "generated"

    result = pipeline._finalize_carrier_observation(SimpleNamespace())
    assert result == {"status": "receipt_issued", "receipt": RECEIPT_FILENAME}
    receipt = parse_carrier_observation_receipt(
        json.loads(pipeline._carrier_receipt_path().read_text(encoding="utf-8")),
        now_epoch=now + 60,
    )
    assert receipt["validated_sha"] == SHA
    assert receipt["collected_epoch"] == now
    assert not pipeline._private("carrier-observation-result.json").exists()


def test_receipt_requires_verified_binding(tmp_path: Path, monkeypatch) -> None:
    now = int(time.time())
    pipeline = _staged_pipeline(tmp_path, _aggregate(_candidate(), now))
    _bind_env(monkeypatch)
    assert pipeline._carrier_binding_passed is False
    assert pipeline._finalize_carrier_observation(SimpleNamespace()) == {
        "status": "skipped",
        "reason": "candidate_binding_not_verified",
    }
    assert not pipeline._carrier_receipt_path().exists()


def test_binding_failure_stays_local_and_never_persists(tmp_path: Path, monkeypatch) -> None:
    now = int(time.time())
    pipeline = _staged_pipeline(tmp_path, _aggregate(_candidate(), now))
    _bind_env(monkeypatch)
    changed = _candidate()
    changed["proxy-providers"]["cr_browsing"]["payload"][0]["server"] = "new.fixture.invalid"
    monkeypatch.setattr(
        "clash_relay.production_lifecycle.build_candidate", lambda **_kw: _generated(changed)
    )
    with pytest.raises(ValidationError, match="current candidate"):
        pipeline._generate()
    assert not pipeline._carrier_receipt_path().exists()
    # The failure remains a local diagnostic and a step-summary note.
    diagnostic = json.loads(
        pipeline._private("carrier-binding-failure.json").read_text(encoding="utf-8")
    )
    assert diagnostic == {"status": "binding_failed", "reason": "candidate_binding_not_verified"}
    summary = pipeline._private("carrier-binding-failure.md").read_text(encoding="utf-8")
    assert "binding failure" in summary.lower()
    for secret in (KEY.decode(), "fixture.invalid", "private-password"):
        assert secret not in summary


def test_preflight_without_carrier_input_skips_history(tmp_path: Path, monkeypatch) -> None:
    pipeline = ProductionPreflightPipeline(ProductionLifecyclePaths.canonical(tmp_path))
    monkeypatch.delenv("CLASH_RELAY_CARRIER_HMAC_KEY", raising=False)
    assert pipeline._finalize_carrier_observation(SimpleNamespace()) == {"status": "not_configured"}


def test_receipt_write_never_contains_private_material(tmp_path: Path, monkeypatch) -> None:
    now = int(time.time())
    pipeline = _staged_pipeline(tmp_path, _aggregate(_candidate(), now))
    _bind_env(monkeypatch)
    monkeypatch.setenv("GITHUB_SHA", SHA)
    monkeypatch.setattr(
        "clash_relay.production_lifecycle.build_candidate", lambda **_kw: _generated(_candidate())
    )
    pipeline._generate()
    pipeline._finalize_carrier_observation(SimpleNamespace())
    receipt_text = pipeline._carrier_receipt_path().read_text(encoding="utf-8")
    for secret in (KEY.decode(), "fixture.invalid", "private-password", "sub_1/"):
        assert secret not in receipt_text


class ProductionPipelineForTest(ProductionPipeline):
    """Publishing variant with the optional post-release stages disabled."""

    def _persist_derived_state(self, project):
        return {"status": "skipped", "reason": "test"}

    def _persist_production_metrics(self, project):
        return {"status": "skipped", "reason": "test"}

    def _publish_scheduler_observation(self, project, *, metrics):
        return {"status": "skipped", "reason": "test"}

    def _record_operational_slo(self, **_kwargs):
        return {"status": "skipped", "reason": "test"}


def _full_run_pipeline(
    tmp_path: Path, *, publish: bool, receipt: dict, monkeypatch, preflight: bool = False
) -> ProductionPipeline:
    paths = ProductionLifecyclePaths.canonical(
        tmp_path, carrier_qualification_input=tmp_path / "carrier-input.json"
    )
    for declaration in (paths.config, paths.subscriptions, paths.policies):
        declaration.write_text("fixture: true\n", encoding="utf-8")
    (tmp_path / "carrier-input.json").write_text("{}", encoding="utf-8")
    pipeline = (
        ProductionPreflightPipeline(paths)
        if preflight
        else ProductionPipelineForTest(paths, publish=publish)
    )
    project = SimpleNamespace(config={})
    binary = tmp_path / "fixture-mihomo"
    binary.write_bytes(b"fixture")

    monkeypatch.setattr(lifecycle.ProjectPaths, "load", lambda _self: project)
    monkeypatch.setattr(lifecycle, "publication_gate", lambda *_a, **_k: None)

    def stage_carrier_input() -> None:
        snapshot = paths.private_dir / "carrier-qualification.json"
        snapshot.write_text("{}", encoding="utf-8")
        pipeline._carrier_input_snapshot = snapshot
        pipeline._carrier_binding_passed = True

    monkeypatch.setattr(pipeline, "_stage_carrier_input", stage_carrier_input)
    monkeypatch.setattr(pipeline, "_generate", lambda: {"status": "generated"})
    monkeypatch.setattr(pipeline, "_load_derived_state", lambda _project: None)
    monkeypatch.setattr(pipeline, "_download_primary_mihomo", lambda: binary)
    monkeypatch.setattr(
        pipeline, "_qualify", lambda _b: {"production_pipeline": {"status": "passed"}}
    )
    monkeypatch.setattr(
        pipeline,
        "_release_candidate_stage",
        lambda _project, _binary: SimpleNamespace(
            promotion={"status": "passed"},
            matrix={"status": "passed"},
            release={"status": "published"} if publish else None,
            timings_ms={},
        ),
    )
    monkeypatch.setattr(pipeline, "_post_commit_proof", lambda *, release: {"status": "passed"})
    monkeypatch.setattr(
        pipeline,
        "_post_commit_manifest",
        lambda **_kwargs: {"release_id": "fixture", "config_sha256": "0" * 64},
    )
    monkeypatch.setattr(pipeline, "_write_lifecycle_observability", lambda _progress: None)
    monkeypatch.setattr(
        lifecycle, "build_carrier_observation_receipt", lambda **_kwargs: dict(receipt)
    )
    return pipeline


def test_publish_issues_receipt_only_after_release_gate(tmp_path: Path, monkeypatch) -> None:
    order: list[str] = []
    pipeline = _full_run_pipeline(
        tmp_path, publish=True, receipt={"receipt_schema_version": 1}, monkeypatch=monkeypatch
    )
    original_release = pipeline._release_candidate_stage

    def release_stage(project, binary):
        order.append("release_gate")
        return original_release(project, binary)

    monkeypatch.setattr(pipeline, "_release_candidate_stage", release_stage)
    original_receipt = pipeline._finalize_carrier_observation

    def issue_receipt(project):
        order.append("receipt")
        return original_receipt(project)

    monkeypatch.setattr(pipeline, "_finalize_carrier_observation", issue_receipt)
    result = pipeline.run()
    assert result["status"] == "passed"
    assert order == ["release_gate", "receipt"]
    assert result["carrier_observation_history"] == {
        "status": "receipt_issued",
        "receipt": RECEIPT_FILENAME,
    }
    assert pipeline._carrier_receipt_path().is_file()
    assert not pipeline._private("carrier-observation-result.json").exists()


def test_bound_preflight_receipt_then_explicit_commit_is_idempotent(
    tmp_path: Path, monkeypatch
) -> None:
    now = int(time.time())
    candidate = _candidate()
    aggregate = _aggregate(candidate, now)
    pipeline = _full_run_pipeline(
        tmp_path, publish=False, receipt={}, monkeypatch=monkeypatch, preflight=True
    )

    def stage_carrier_input() -> None:
        snapshot = pipeline._private("carrier-qualification.json")
        snapshot.write_text(json.dumps(aggregate), encoding="utf-8")
        pipeline._carrier_input_snapshot = snapshot
        pipeline._carrier_binding_passed = False

    monkeypatch.setattr(pipeline, "_stage_carrier_input", stage_carrier_input)
    monkeypatch.setattr(lifecycle, "build_candidate", lambda **_kw: _generated(candidate))
    monkeypatch.setattr(pipeline, "_generate", ProductionPipeline._generate.__get__(pipeline))
    monkeypatch.setattr(
        lifecycle, "build_carrier_observation_receipt", build_carrier_observation_receipt
    )
    _bind_env(monkeypatch)
    monkeypatch.setenv("GITHUB_SHA", SHA)
    result = pipeline.run()
    assert result["status"] == "passed"
    assert result["publication_status"] == "preflight"
    assert result["carrier_observation_history"]["status"] == "receipt_issued"
    receipt = json.loads(pipeline._carrier_receipt_path().read_text(encoding="utf-8"))

    storage: dict[str, bytes] = {}
    calls: list[str] = []

    class Publisher:
        def __init__(self, **kwargs):
            self.key_name = kwargs["key_name"]

        def read(self):
            calls.append("read")
            return storage.get(self.key_name)

        def publish(self, *, content):
            calls.append("publish")
            storage[self.key_name] = content
            return {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}

    monkeypatch.setattr("clash_relay.carrier_history_application.CloudflareKVPublisher", Publisher)
    env = {
        **os.environ,
        "CLOUDFLARE_API_TOKEN": "fixture-token",
        "CLOUDFLARE_ACCOUNT_ID": "fixture-account",
        "CLOUDFLARE_KV_NAMESPACE_TITLE": "fixture-namespace",
    }
    project = SimpleNamespace(config={"publishing": {"cloudflare_kv": {"key": "config"}}})
    first = commit_carrier_observation_receipt(
        project=project, receipt=receipt, env=env, now_epoch=now + 1
    )
    second = commit_carrier_observation_receipt(
        project=project, receipt=receipt, env=env, now_epoch=now + 2
    )
    assert first["status"] == "published"
    assert second["status"] == "unchanged"
    assert calls == ["read", "publish", "read"]
    persisted = storage["config.carrier-observation-history-v1"]
    assert b"fixture.invalid" not in persisted
    assert KEY.decode().encode() not in persisted


def test_qualification_failure_issues_no_receipt_or_history_write(
    tmp_path: Path, monkeypatch
) -> None:
    pipeline = _full_run_pipeline(
        tmp_path, publish=False, receipt={"receipt_schema_version": 1}, monkeypatch=monkeypatch
    )

    def failed_qualification(_binary):
        raise ValidationError("qualification failed")

    monkeypatch.setattr(pipeline, "_qualify", failed_qualification)
    with pytest.raises(ValidationError, match="qualification failed"):
        pipeline.run()
    assert not pipeline._carrier_receipt_path().exists()


def test_publish_receipt_failure_does_not_break_verified_release(
    tmp_path: Path, monkeypatch
) -> None:
    pipeline = _full_run_pipeline(
        tmp_path, publish=True, receipt={"receipt_schema_version": 1}, monkeypatch=monkeypatch
    )

    def broken_receipt(**_kwargs):
        raise ValidationError("receipt unavailable")

    monkeypatch.setattr(lifecycle, "build_carrier_observation_receipt", broken_receipt)
    result = pipeline.run()
    assert result["status"] == "passed"
    assert result["release_status"] == "published"
    assert result["carrier_observation_history"] == {
        "status": "unavailable",
        "reason": "stage_failed",
    }
    assert "issue_carrier_observation_receipt" in result["warnings"]
    assert not pipeline._carrier_receipt_path().exists()


def test_publish_without_carrier_input_never_commits(tmp_path: Path, monkeypatch) -> None:
    paths = ProductionLifecyclePaths.canonical(tmp_path)
    for declaration in (paths.config, paths.subscriptions, paths.policies):
        declaration.write_text("fixture: true\n", encoding="utf-8")
    pipeline = ProductionPipelineForTest(paths, publish=True)
    binary = tmp_path / "fixture-mihomo"
    binary.write_bytes(b"fixture")
    project = SimpleNamespace(config={})

    monkeypatch.setattr(lifecycle.ProjectPaths, "load", lambda _self: project)
    monkeypatch.setattr(lifecycle, "publication_gate", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "_generate", lambda: {"status": "generated"})
    monkeypatch.setattr(pipeline, "_load_derived_state", lambda _project: None)
    monkeypatch.setattr(pipeline, "_download_primary_mihomo", lambda: binary)
    monkeypatch.setattr(
        pipeline, "_qualify", lambda _b: {"production_pipeline": {"status": "passed"}}
    )
    monkeypatch.setattr(
        pipeline,
        "_release_candidate_stage",
        lambda _project, _binary: SimpleNamespace(
            promotion={"status": "passed"},
            matrix={"status": "passed"},
            release={"status": "published"},
            timings_ms={},
        ),
    )
    monkeypatch.setattr(pipeline, "_post_commit_proof", lambda *, release: {"status": "passed"})
    monkeypatch.setattr(
        pipeline,
        "_post_commit_manifest",
        lambda **_kwargs: {"release_id": "fixture", "config_sha256": "0" * 64},
    )
    monkeypatch.setattr(pipeline, "_write_lifecycle_observability", lambda _progress: None)

    result = pipeline.run()
    assert result["status"] == "passed"
    assert result["carrier_observation_history"] == {"status": "not_configured"}
