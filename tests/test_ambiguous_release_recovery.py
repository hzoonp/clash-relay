from __future__ import annotations

import json

from clash_relay.production_lifecycle import ProductionLifecyclePaths, ProductionPipeline


def test_unknown_release_retains_only_exact_reconciliation_inputs(tmp_path, monkeypatch) -> None:
    paths = ProductionLifecyclePaths.canonical(tmp_path)
    paths.private_dir.mkdir(parents=True)
    (paths.private_dir / "config.yaml").write_bytes(b"candidate\n")
    (paths.private_dir / "current-production.yaml").write_bytes(b"previous\n")
    (paths.private_dir / "current-production.captured").write_text("captured\n")
    (paths.private_dir / "scheduler-history.key").write_text("secret", encoding="ascii")
    monkeypatch.setattr(
        "clash_relay.production_lifecycle.reconcile_production_release",
        lambda **kwargs: {"status": "unknown"},
    )

    result = ProductionPipeline(paths, publish=True)._preserve_ambiguous_release(object())
    recovery = next((paths.work_dir / "recovery").iterdir())
    metadata = json.loads((recovery / "metadata.json").read_text(encoding="utf-8"))
    assert result == "preserved"
    assert sorted(path.name for path in recovery.iterdir()) == [
        "candidate.yaml",
        "metadata.json",
        "previous.yaml",
    ]
    assert (recovery / "candidate.yaml").read_bytes() == b"candidate\n"
    assert (recovery / "previous.yaml").read_bytes() == b"previous\n"
    assert metadata["reconciliation_status"] == "unknown"


def test_proven_release_does_not_leave_private_recovery_copy(tmp_path, monkeypatch) -> None:
    paths = ProductionLifecyclePaths.canonical(tmp_path)
    paths.private_dir.mkdir(parents=True)
    (paths.private_dir / "config.yaml").write_bytes(b"candidate\n")
    (paths.private_dir / "current-production.captured").write_text("captured\n")
    monkeypatch.setattr(
        "clash_relay.production_lifecycle.reconcile_production_release",
        lambda **kwargs: {"status": "committed"},
    )

    assert (
        ProductionPipeline(paths, publish=True)._preserve_ambiguous_release(object()) == "committed"
    )
    assert not (paths.work_dir / "recovery").exists()


def test_hostname_observability_drops_private_and_unexpected_fields() -> None:
    result = ProductionPipeline._safe_hostname_observability(
        {
            "proxy_host_resolution": {
                "by_source_failure_category": {
                    "subscription_4": {"nxdomain": 1, "no_answer": 0, "server": "secret"},
                    "bad host.example": {"nxdomain": 5},
                },
                "probe_duration_ms": 123.5,
                "unique_hostnames_probed": 2,
                "hostname": "private.example",
            }
        }
    )
    assert result == {
        "hostname_failure_categories_by_source": {
            "subscription_4": {"nxdomain": 1, "no_answer": 0}
        },
        "hostname_probe_duration_ms": 123.5,
        "unique_hostnames_probed": 2,
    }
