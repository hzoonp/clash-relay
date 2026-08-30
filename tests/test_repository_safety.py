from __future__ import annotations

import subprocess
from pathlib import Path

import yaml


def test_repository_audit_passes(repo_root: Path) -> None:
    result = subprocess.run(
        ["python", "scripts/repository_audit.py"],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert result.returncode == 0, result.stdout


def test_secret_and_generated_paths_are_ignored(repo_root: Path) -> None:
    ignored = [
        ".env",
        ".env.production",
        ".secrets/subscriptions.yaml",
        "secrets.yaml",
        "subscriptions.secret.json",
        "dist/config.yaml",
        ".work/candidate.yaml",
        "providers/cache.yaml",
    ]
    for value in ignored:
        result = subprocess.run(
            ["git", "check-ignore", "-q", value], cwd=repo_root, check=False
        )
        assert result.returncode == 0, value


def test_public_per_fork_declarations_are_not_ignored(repo_root: Path) -> None:
    for value in ["config.yaml", "subscriptions.yaml"]:
        result = subprocess.run(
            ["git", "check-ignore", "-q", value], cwd=repo_root, check=False
        )
        assert result.returncode == 1, value


def test_lock_files_pin_every_dependency(repo_root: Path) -> None:
    for filename in ["requirements.lock", "requirements-dev.lock"]:
        for raw in (repo_root / filename).read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("-r "):
                continue
            assert "==" in line, f"unlocked dependency in {filename}: {line}"


def test_workflows_parse_as_yaml(repo_root: Path) -> None:
    for path in (repo_root / ".github/workflows").glob("*.yml"):
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        assert isinstance(document, dict)
        assert "jobs" in document


def test_stable_workflows_have_no_always_publication_path(repo_root: Path) -> None:
    for name in ("ci.yml", "publish.yml"):
        workflow = (repo_root / ".github" / "workflows" / name).read_text(
            encoding="utf-8"
        )
        assert "always()" not in workflow
        assert "continue-on-error" not in workflow

    publish = (repo_root / ".github" / "workflows" / "publish.yml").read_text(
        encoding="utf-8"
    )
    required_needs = """needs:
      - prepare
      - candidate
      - validate"""
    assert required_needs in publish
    assert "--candidate .work/candidate/config.yaml" in publish
    assert 'gh release create "$tag"' in publish
    assert "--draft" in publish
    assert 'gh release upload "$tag"' in publish
    assert 'gh release edit "$tag" --draft=false --latest' in publish


def test_public_release_and_gist_have_two_key_runtime_gates(repo_root: Path) -> None:
    publish = (repo_root / ".github" / "workflows" / "publish.yml").read_text(
        encoding="utf-8"
    )
    assert "needs.promote.outputs.release_enabled == 'true'" in publish
    assert "vars.PUBLISH_PUBLIC_RELEASE == 'true'" in publish
    assert "needs.promote.outputs.gist_enabled == 'true'" in publish
    assert "vars.PUBLISH_UNLISTED_GIST == 'true'" in publish
    assert publish.count("CLASH_RELAY_PUBLICATION_ACKNOWLEDGEMENT") >= 2
