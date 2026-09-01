from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import clash_relay.qualification_pipeline as pipeline
import scripts.validate_mihomo_matrix as matrix


def test_qualification_pipeline_reports_only_aggregate_phase_timings(
    tmp_path: Path, monkeypatch
) -> None:
    candidate = tmp_path / "generated.yaml"
    candidate.write_text("test: true\n", encoding="utf-8")

    monkeypatch.setattr(
        pipeline,
        "_artifact",
        lambda path, stage: SimpleNamespace(fingerprint=f"fingerprint-{stage}"),
    )

    def fake_stage(name, command):
        if name == "AI":
            return {"status": "qualified", "diagnostics": {"qualification_mode": "live"}}
        return {"status": "qualified", "automatic_nodes": 3}

    monkeypatch.setattr(pipeline, "_run_json_stage", fake_stage)
    result = pipeline.run_qualification_pipeline(
        candidate=candidate,
        output=tmp_path / "out" / "config.yaml",
        policies=tmp_path / "policies.yaml",
        mihomo_bin=tmp_path / "mihomo",
        stage_dir=tmp_path / "stages",
        browsing_report=tmp_path / "reports" / "browsing.json",
        ai_report=tmp_path / "reports" / "ai.json",
    )

    assert result["status"] == "qualified"
    assert set(result["timings_ms"]) == {"browsing_transport", "ai", "total"}
    assert all(isinstance(value, float) and value >= 0 for value in result["timings_ms"].values())
    serialized = json.dumps(result)
    assert "command" not in serialized
    assert "stderr" not in serialized


def test_stable_matrix_reuses_primary_and_downloads_only_remaining_core(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    candidate = tmp_path / "config.yaml"
    candidate.write_text("test: true\n", encoding="utf-8")
    primary = tmp_path / "mihomo-primary"
    primary.write_text("binary", encoding="utf-8")
    downloads: list[str] = []

    monkeypatch.setattr(matrix, "load_mihomo_tags", lambda manifest, channel: ("v1", "v2"))

    def fake_download(tag, *, manifest, channel, work_dir):
        downloads.append(tag)
        path = work_dir / tag
        path.write_text("binary", encoding="utf-8")
        return path

    monkeypatch.setattr(matrix, "_download", fake_download)
    monkeypatch.setattr(
        matrix,
        "validate_with_mihomo",
        lambda binary, candidate, startup_seconds: {"status": "passed"},
    )

    assert matrix.main(
        [
            "--candidate",
            str(candidate),
            "--work-dir",
            str(tmp_path / "cores"),
            "--reuse-primary-bin",
            str(primary),
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert downloads == ["v2"]
    assert result["validated_cores"] == ["v1", "v2"]
    assert result["downloaded_cores"] == 1
    assert result["results"][0]["reused_primary"] is True
