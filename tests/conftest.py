from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest
import yaml

from clash_relay.builder import build_candidate
from clash_relay.util import dump_yaml


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def project_paths(repo_root: Path) -> dict[str, Path]:
    root = repo_root / "tests/fixtures/project"
    return {
        "config_path": root / "config.yaml",
        "subscriptions_path": root / "subscriptions.yaml",
        "services_path": root / "services.yaml",
        "policies_path": root / "policies.yaml",
    }


@pytest.fixture(scope="session")
def fixture_env(repo_root: Path) -> dict[str, str]:
    source = repo_root / "tests/fixtures/subscriptions"
    return {
        "SUB_PRIMARY": (source / "primary.yaml").resolve().as_uri(),
        "SUB_SECONDARY": (source / "secondary.yaml").resolve().as_uri(),
        "SUB_SPECIAL": (source / "special.yaml").resolve().as_uri(),
    }


@pytest.fixture(scope="session")
def built_candidate(project_paths: dict[str, Path], fixture_env: dict[str, str]):
    return build_candidate(**project_paths, env=fixture_env)


@pytest.fixture
def project_factory(tmp_path: Path, repo_root: Path):
    counter = 0

    def factory() -> tuple[Path, dict[str, Path]]:
        nonlocal counter
        counter += 1
        root = tmp_path / f"project-{counter}"
        shutil.copytree(repo_root / "tests/fixtures/project", root)
        return root, {
            "config_path": root / "config.yaml",
            "subscriptions_path": root / "subscriptions.yaml",
            "services_path": root / "services.yaml",
            "policies_path": root / "policies.yaml",
        }

    return factory


@pytest.fixture
def yaml_editor():
    def edit(path: Path, callback):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        callback(data)
        path.write_text(dump_yaml(data), encoding="utf-8")
        return copy.deepcopy(data)

    return edit
