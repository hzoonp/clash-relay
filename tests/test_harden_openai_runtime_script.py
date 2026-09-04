from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_script(repo_root: Path):
    path = repo_root / "scripts" / "harden_openai_runtime.py"
    spec = importlib.util.spec_from_file_location("test_harden_openai_runtime_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_operation_status_cannot_override_pipeline_success(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _load_script(repo_root)
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text("proxy-groups: []\nproxy-providers: {}\n", encoding="utf-8")
    monkeypatch.setattr(
        module,
        "harden_openai_client_path",
        lambda path: {
            "status": "passed",
            "runtime_status": "hardened",
            "selection": "stable_first_fallback",
            "runtime_regions": 2,
            "runtime_providers": 2,
            "runtime_nodes": 4,
        },
    )
    monkeypatch.setattr(sys, "argv", ["harden_openai_runtime.py", "--candidate", str(candidate)])

    assert module.main() == 0
    result = json.loads(capsys.readouterr().out)

    assert result["status"] == "passed"
    assert result["runtime_status"] == "hardened"
    assert result["selection"] == "stable_first_fallback"
    assert result["runtime_regions"] == 2
