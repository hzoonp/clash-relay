from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest
import yaml

from clash_relay.builder import build_candidate
from clash_relay.util import dump_yaml

_POLICY_SECTION_OWNERS = {
    "routing": "routing",
    "scheduler": "scheduling",
    "probes": "scheduling",
    "capabilities": "classification",
    "cost_levels": "classification",
    "country_classification": "classification",
    "pools": "topology",
    "chains": "topology",
}


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def project_paths(repo_root: Path) -> dict[str, Path]:
    root = repo_root / "tests/fixtures/project"
    return {
        "config_path": root / "config.yaml",
        "subscriptions_path": root / "subscriptions.yaml",
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
            "policies_path": root / "policies.yaml",
        }

    return factory


@pytest.fixture
def yaml_editor():
    def edit(path: Path, callback):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("version") == 2 and isinstance(
            data.get("fragments"), dict
        ):
            fragment_docs: dict[str, dict] = {}
            merged: dict = {"version": 1}
            for name, relative in data["fragments"].items():
                target = path.parent / str(relative)
                fragment = yaml.safe_load(target.read_text(encoding="utf-8"))
                if fragment is None:
                    fragment = {}
                fragment_docs[str(name)] = fragment
                merged.update(fragment)
            callback(merged)
            for name, relative in data["fragments"].items():
                document = {
                    key: value
                    for key, value in merged.items()
                    if key != "version" and _POLICY_SECTION_OWNERS.get(key) == name
                }
                target = path.parent / str(relative)
                target.write_text(
                    yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
                    encoding="utf-8",
                )
            return copy.deepcopy(merged)

        callback(data)
        path.write_text(dump_yaml(data), encoding="utf-8")
        return copy.deepcopy(data)

    return edit
