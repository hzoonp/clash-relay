from __future__ import annotations

import os
from pathlib import Path

import pytest

from clash_relay.builder import build_candidate
from clash_relay.mihomo import validate_with_mihomo

pytestmark = pytest.mark.integration


def _binary() -> Path:
    value = os.environ.get("MIHOMO_BIN")
    if not value:
        pytest.skip("MIHOMO_BIN is not set")
    return Path(value)


def test_generated_fictional_candidate_loads_and_starts(
    project_paths, fixture_env, tmp_path: Path
) -> None:
    result = build_candidate(**project_paths, env=fixture_env)
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text(result.yaml_text, encoding="utf-8")
    report = validate_with_mihomo(_binary(), candidate, startup_seconds=1.0)
    assert report["config_test"] == "passed"
    assert report["startup_smoke"] == "passed"


def test_real_core_validation_is_deterministic(project_paths, fixture_env, tmp_path: Path) -> None:
    first = build_candidate(**project_paths, env=fixture_env)
    second = build_candidate(**project_paths, env=fixture_env)
    assert first.yaml_text == second.yaml_text
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text(first.yaml_text, encoding="utf-8")
    assert validate_with_mihomo(_binary(), candidate)["config_test"] == "passed"
