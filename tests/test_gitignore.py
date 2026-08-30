from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _is_ignored(path: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--", path],
        cwd=ROOT,
        check=False,
    )
    return result.returncode == 0


def test_canonical_public_configuration_is_trackable() -> None:
    assert not _is_ignored("config.yaml")
    assert not _is_ignored("subscriptions.yaml")


def test_private_and_machine_local_files_are_ignored() -> None:
    assert _is_ignored(".secrets/subscriptions.yaml")
    assert _is_ignored("config.local.yaml")
    assert _is_ignored("subscriptions.local.yaml")
    assert _is_ignored("dist/private-config.yaml")
