from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import clash_relay.production_pipeline as pipeline
from clash_relay.errors import ValidationError
from clash_relay.production_pipeline import (
    ProductionPipelineOutputs,
    ProjectPaths,
    QualificationPaths,
    run_production_pipeline,
)


def test_pipeline_json_helpers_fail_closed_and_write_deterministically(tmp_path) -> None:
    valid = tmp_path / "valid.json"
    valid.write_text('{"b": 2, "a": 1}', encoding="utf-8")
    assert pipeline._load_json(valid) == {"a": 1, "b": 2}

    not_object = tmp_path / "list.json"
    not_object.write_text("[]", encoding="utf-8")
    with pytest.raises(ValidationError, match="must be an object"):
        pipeline._load_json(not_object)

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(ValidationError, match="could not read JSON input"):
        pipeline._load_json(invalid)

    output = tmp_path / "written.json"
    pipeline._write_json(output, {"b": 2, "a": 1})
    assert output.read_text(encoding="utf-8") == '{\n  "a": 1,\n  "b": 2\n}\n'


def test_run_production_pipeline_owns_stage_order_and_aggregate_summary(
    monkeypatch, tmp_path
) -> None:
    events: list[str] = []
    project = SimpleNamespace(acl4ssr=None)
    project_paths = ProjectPaths(
        config=tmp_path / "config.yaml",
        subscriptions=tmp_path / "subscriptions.yaml",
        policies=tmp_path / "policies.yaml",
    )
    qualification_paths = QualificationPaths(
        candidate=tmp_path / "generated.yaml",
        output=tmp_path / "qualified.yaml",
        mihomo_bin=tmp_path / "mihomo",
        stage_dir=tmp_path / "stages",
        browsing_report=tmp_path / "browsing.json",
        ai_report=tmp_path / "ai.json",
    )
    outputs = ProductionPipelineOutputs(
        pre_audit=tmp_path / "pre.json",
        post_audit=tmp_path / "post.json",
        qualification=tmp_path / "qualification.json",
        summary_markdown=tmp_path / "summary.md",
    )
    build_report = tmp_path / "build.json"
    build_report.write_text('{"subscriptions": []}', encoding="utf-8")
    qualification_paths.browsing_report.write_text('{"diagnostics": {}}', encoding="utf-8")
    qualification_paths.ai_report.write_text('{"diagnostics": {}}', encoding="utf-8")

    monkeypatch.setattr(ProjectPaths, "load", lambda self: project)

    candidates = iter(({"stage": "generated"}, {"stage": "qualified"}))
    monkeypatch.setattr(pipeline, "load_candidate", lambda _path: next(candidates))

    def fake_audit(_project, candidate, *, build_report=None):
        events.append(f"audit:{candidate['stage']}")
        assert build_report == {"subscriptions": []}
        return {"status": "passed", "routing_v2": {"status": "passed"}}

    def fake_qualification(**kwargs):
        events.append("qualify")
        assert kwargs["candidate"] == qualification_paths.candidate
        assert kwargs["output"] == qualification_paths.output
        return {"status": "qualified", "browsing": {"stage_attempts": 1}}

    monkeypatch.setattr(pipeline, "audit_candidate", fake_audit)
    monkeypatch.setattr(pipeline, "run_qualification_pipeline", fake_qualification)
    monkeypatch.setattr(
        pipeline,
        "render_production_summary_markdown",
        lambda _report: "PRODUCTION\n",
    )
    monkeypatch.setattr(
        pipeline,
        "render_qualification_summary_markdown",
        lambda _browsing, _ai: "QUALIFICATION\n",
    )

    result = run_production_pipeline(
        project_paths=project_paths,
        qualification_paths=qualification_paths,
        outputs=outputs,
        build_report_path=build_report,
        workers=7,
    )

    assert events == ["audit:generated", "qualify", "audit:qualified"]
    assert result["status"] == "qualified"
    assert result["production_pipeline"] == {
        "status": "passed",
        "pre_audit": "passed",
        "post_audit": "passed",
        "routing_v2": "passed",
    }
    assert json.loads(outputs.pre_audit.read_text(encoding="utf-8"))["status"] == "passed"
    assert json.loads(outputs.post_audit.read_text(encoding="utf-8"))["status"] == "passed"
    assert json.loads(outputs.qualification.read_text(encoding="utf-8"))["status"] == "qualified"
    assert outputs.summary_markdown.read_text(encoding="utf-8") == "PRODUCTION\n\nQUALIFICATION\n"
