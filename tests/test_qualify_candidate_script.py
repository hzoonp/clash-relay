from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from pathlib import Path

import pytest


def _load_script(repo_root: Path):
    path = repo_root / "scripts" / "qualify_candidate.py"
    spec = importlib.util.spec_from_file_location("test_qualify_candidate_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _argv(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "qualify_candidate.py",
        "--candidate",
        str(tmp_path / "candidate.yaml"),
        "--output",
        str(tmp_path / "qualified.yaml"),
        "--policies",
        str(tmp_path / "policies.yaml"),
        "--mihomo-bin",
        str(tmp_path / "mihomo"),
        "--stage-dir",
        str(tmp_path / "stages"),
        "--browsing-report",
        str(tmp_path / "browsing.json"),
        "--ai-report",
        str(tmp_path / "ai.json"),
        *extra,
    ]


def test_adapter_arguments_bind_to_the_pipeline_signature(
    repo_root: Path, tmp_path: Path, monkeypatch
) -> None:
    """The adapter must call the real pipeline, not a stale keyword set.

    ``script_dir`` was removed from ``run_qualification_pipeline`` when the
    pipeline stopped launching sibling scripts. Binding the adapter's keyword
    arguments to the live signature catches that drift without executing the
    network-dependent pipeline.
    """
    from clash_relay.qualification_pipeline import run_qualification_pipeline

    module = _load_script(repo_root)
    captured: dict[str, object] = {}

    def fake_pipeline(**kwargs: object) -> dict[str, object]:
        inspect.signature(run_qualification_pipeline).bind(**kwargs)
        captured.update(kwargs)
        return {"status": "passed"}

    monkeypatch.setattr(module, "run_qualification_pipeline", fake_pipeline)
    monkeypatch.setattr(sys, "argv", _argv(tmp_path, "--workers", "3"))

    assert module.main() == 0

    assert captured["workers"] == 3
    assert set(captured) == {
        "candidate",
        "output",
        "policies",
        "mihomo_bin",
        "stage_dir",
        "browsing_report",
        "ai_report",
        "workers",
        "history",
        "history_key",
        "next_history",
        "cache",
        "cache_key",
        "next_cache",
    }


def test_a_stale_keyword_argument_fails_the_binding_check(
    repo_root: Path, tmp_path: Path, monkeypatch
) -> None:
    """The negative case: the removed ``script_dir`` keyword must not bind."""
    from clash_relay.qualification_pipeline import run_qualification_pipeline

    with pytest.raises(TypeError, match="script_dir"):
        inspect.signature(run_qualification_pipeline).bind(
            candidate=tmp_path / "candidate.yaml",
            output=tmp_path / "qualified.yaml",
            policies=tmp_path / "policies.yaml",
            mihomo_bin=tmp_path / "mihomo",
            stage_dir=tmp_path / "stages",
            browsing_report=tmp_path / "browsing.json",
            ai_report=tmp_path / "ai.json",
            script_dir=tmp_path,
        )


def test_parser_rejects_a_missing_required_argument(repo_root: Path) -> None:
    module = _load_script(repo_root)

    with pytest.raises(SystemExit) as excinfo:
        module.build_parser().parse_args(["--candidate", "candidate.yaml"])

    assert excinfo.value.code == 2


def test_pipeline_rejection_keeps_the_adapter_exit_code(
    repo_root: Path, tmp_path: Path, monkeypatch, capsys
) -> None:
    from clash_relay.errors import ValidationError

    module = _load_script(repo_root)

    def rejecting_pipeline(**kwargs: object) -> dict[str, object]:
        raise ValidationError("qualification stage rejected the candidate")

    monkeypatch.setattr(module, "run_qualification_pipeline", rejecting_pipeline)
    monkeypatch.setattr(sys, "argv", _argv(tmp_path))

    assert module.main() == 2
    assert "qualification stage rejected the candidate" in capsys.readouterr().err


def test_successful_run_prints_one_json_object(
    repo_root: Path, tmp_path: Path, monkeypatch, capsys
) -> None:
    module = _load_script(repo_root)
    monkeypatch.setattr(
        module,
        "run_qualification_pipeline",
        lambda **kwargs: {"status": "passed", "stages": ["browsing", "ai"]},
    )
    monkeypatch.setattr(sys, "argv", _argv(tmp_path))

    assert module.main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "stages": ["browsing", "ai"],
        "status": "passed",
    }
