from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_authoritative_documentation_matches_current_production_contract() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/audit_documentation_contract.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "documentation contract: passed"
