"""End-to-end tests for the Phase-B commit entrypoint script."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from clash_relay.carrier_observation_receipt import (
    RECEIPT_FILENAME,
    build_carrier_observation_receipt,
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "persist_carrier_observation.py"
SHA = "c" * 40
ENV = {
    "CLOUDFLARE_API_TOKEN": "private-token",
    "CLOUDFLARE_ACCOUNT_ID": "account",
    "CLOUDFLARE_KV_NAMESPACE_TITLE": "namespace",
    "GITHUB_SHA": SHA,
    "CLASH_RELAY_CARRIER_HMAC_KEY": "repository-bound-carrier-receipt-key-123456",
    "GITHUB_REPOSITORY": "hzoonp/clash-relay",
}


def _load_runner():
    spec = importlib.util.spec_from_file_location("persist_carrier_observation_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner


def _aggregate(now: int) -> dict:
    row = {
        "sampled": 6,
        "sampled_tcp_endpoints": 6,
        "tested": 6,
        "reachable": 6,
        "skipped_unsupported": 1,
        "skipped_udp_native_endpoints": 1,
        "geographic_regions_sampled": 2,
        "protocols_sampled": 1,
        "sources_sampled": 1,
        "strata_sampled": 2,
        "eligible_tcp_endpoints": 6,
        "sufficient_evidence": True,
        "median_latency_ms": 40.0,
        "p90_latency_ms": 55.0,
        "outcomes": {
            "dns_failure": 0,
            "connect_timeout": 0,
            "connection_refused": 0,
            "connect_failure": 0,
            "tcp_connected": 6,
        },
    }
    return {
        "schema_version": 1,
        "collected_at_epoch": now,
        "sample_set_id": "a" * 32,
        "inventory_set_id": "b" * 32,
        "probe_plan_id": "c" * 32,
        "sampler_version": 3,
        "carriers": dict.fromkeys(("telecom", "unicom", "mobile"), row),
    }


def _write_receipt(root: Path, aggregate: dict, *, sha: str = SHA) -> Path:
    receipt = build_carrier_observation_receipt(aggregate=aggregate, validated_sha=sha, env=ENV)
    receipt_path = root / ".work" / RECEIPT_FILENAME
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    return receipt_path


@pytest.fixture
def script_runner(monkeypatch):
    monkeypatch.setenv("CLASH_RELAY_CARRIER_HMAC_KEY", ENV["CLASH_RELAY_CARRIER_HMAC_KEY"])
    monkeypatch.setenv("GITHUB_REPOSITORY", ENV["GITHUB_REPOSITORY"])
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
    return SimpleNamespace(storage=storage, calls=calls)


def test_script_consumes_only_receipt_and_commits_once(
    tmp_path: Path, project_factory, script_runner, monkeypatch
) -> None:
    root, _paths = project_factory()
    now = int(time.time())
    receipt_path = _write_receipt(root, _aggregate(now))
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", ENV["CLOUDFLARE_API_TOKEN"])
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", ENV["CLOUDFLARE_ACCOUNT_ID"])
    monkeypatch.setenv("CLOUDFLARE_KV_NAMESPACE_TITLE", ENV["CLOUDFLARE_KV_NAMESPACE_TITLE"])
    monkeypatch.setenv("GITHUB_SHA", SHA)

    runner = _load_runner()
    assert runner.main(["--root", str(root), "--receipt", str(receipt_path)]) == 0
    assert script_runner.calls == ["read", "publish"]
    persisted = script_runner.storage["production-config.carrier-observation-history-v1"]
    document = json.loads(persisted)
    assert document["schema_version"] == 2
    assert document["carriers"]["telecom"]["campaign_runs_lifetime"] == 1

    # Retrying the same receipt is idempotent: one write total.
    assert runner.main(["--root", str(root), "--receipt", str(receipt_path)]) == 0
    assert script_runner.calls == ["read", "publish", "read"]


def test_script_rejects_bare_carrier_json(
    tmp_path: Path, project_factory, script_runner, monkeypatch
) -> None:
    root, _paths = project_factory()
    now = int(time.time())
    bare = root / ".work" / "carrier-qualification.json"
    bare.parent.mkdir(parents=True, exist_ok=True)
    bare.write_text(json.dumps(_aggregate(now)), encoding="utf-8")
    monkeypatch.setenv("GITHUB_SHA", SHA)

    runner = _load_runner()
    assert runner.main(["--root", str(root), "--receipt", str(bare)]) == 2
    assert script_runner.calls == []


def test_script_fails_closed_on_validated_sha_mismatch(
    tmp_path: Path, project_factory, script_runner, monkeypatch, capsys
) -> None:
    root, _paths = project_factory()
    now = int(time.time())
    receipt_path = _write_receipt(root, _aggregate(now), sha="d" * 40)
    monkeypatch.setenv("GITHUB_SHA", SHA)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)

    runner = _load_runner()
    assert runner.main(["--root", str(root), "--receipt", str(receipt_path)]) == 2
    assert script_runner.calls == []
    captured = capsys.readouterr()
    assert captured.err.strip() == "carrier observation commit failed"
    assert "carrier-qualification" not in captured.out


def test_script_fails_closed_on_receipt_tampering(
    tmp_path: Path, project_factory, script_runner, monkeypatch
) -> None:
    root, _paths = project_factory()
    now = int(time.time())
    receipt_path = _write_receipt(root, _aggregate(now))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["aggregate"]["carriers"]["telecom"]["reachable"] = 0
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setenv("GITHUB_SHA", SHA)

    runner = _load_runner()
    assert runner.main(["--root", str(root), "--receipt", str(receipt_path)]) == 2
    assert script_runner.calls == []


def test_script_failure_output_stays_clean_in_subprocess(tmp_path: Path, project_factory) -> None:
    root, _paths = project_factory()
    now = int(time.time())
    receipt_path = _write_receipt(root, _aggregate(now))
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--receipt", str(receipt_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    # Without GITHUB_SHA or credentials the commit fails closed without
    # echoing receipt contents or aggregate material.
    assert result.returncode == 2
    assert result.stderr.strip() == "carrier observation commit failed"
    assert "sample_set_id" not in result.stderr
