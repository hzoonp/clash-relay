from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import clash_relay.mihomo_matrix_application as matrix
import clash_relay.qualification_pipeline as pipeline


def test_qualification_pipeline_reports_only_aggregate_phase_timings(
    tmp_path: Path, monkeypatch, repo_root: Path
) -> None:
    candidate = tmp_path / "generated.yaml"
    candidate.write_text("test: true\n", encoding="utf-8")

    monkeypatch.setattr(
        pipeline,
        "_artifact",
        lambda path, stage: SimpleNamespace(fingerprint=f"fingerprint-{stage}"),
    )
    monkeypatch.setattr(
        pipeline,
        "run_browsing_qualification",
        lambda **_kwargs: {"status": "qualified", "automatic_nodes": 3},
    )
    monkeypatch.setattr(
        pipeline,
        "run_ai_qualification",
        lambda **_kwargs: {"status": "qualified", "diagnostics": {"qualification_mode": "live"}},
    )
    monkeypatch.setattr(
        pipeline,
        "harden_declared_service_client_paths",
        lambda **_kwargs: {
            "status": "passed",
            "hardened_services": 1,
            "services": {"openai": {"status": "passed"}},
        },
    )
    result = pipeline.run_qualification_pipeline(
        candidate=candidate,
        output=tmp_path / "out" / "config.yaml",
        policies=repo_root / "tests/fixtures/project/policies.yaml",
        mihomo_bin=tmp_path / "mihomo",
        stage_dir=tmp_path / "stages",
        browsing_report=tmp_path / "reports" / "browsing.json",
        ai_report=tmp_path / "reports" / "ai.json",
    )

    assert result["status"] == "qualified"
    assert set(result["timings_ms"]) == {
        "browsing_transport",
        "ai",
        "service_client_path",
        "total",
    }
    assert all(isinstance(value, float) and value >= 0 for value in result["timings_ms"].values())
    serialized = json.dumps(result)
    assert "command" not in serialized
    assert "stderr" not in serialized


def test_stable_matrix_reuses_primary_and_downloads_only_remaining_core(
    tmp_path: Path, monkeypatch
) -> None:
    candidate = tmp_path / "config.yaml"
    candidate.write_text("test: true\n", encoding="utf-8")
    primary = tmp_path / "mihomo-primary"
    primary.write_text("binary", encoding="utf-8")
    downloads: list[str] = []

    monkeypatch.setattr(matrix, "load_mihomo_tags", lambda manifest, channel: ("v1", "v2"))

    def fake_download(*, tag, manifest, channel, output, **_kwargs):
        downloads.append(tag)
        output.write_text("binary", encoding="utf-8")
        return {"tag": tag, "output": str(output)}

    monkeypatch.setattr(matrix, "download_pinned_mihomo", fake_download)
    monkeypatch.setattr(
        matrix,
        "validate_with_mihomo",
        lambda binary, candidate, startup_seconds: {"status": "passed"},
    )

    result = matrix.validate_mihomo_matrix(
        candidate=candidate,
        manifest=tmp_path / "versions.json",
        channel="stable",
        work_dir=tmp_path / "cores",
        reuse_primary_bin=primary,
    )

    assert downloads == ["v2"]
    assert result["validated_cores"] == ["v1", "v2"]
    assert result["downloaded_cores"] == 1
    assert result["results"][0]["reused_primary"] is True
