from __future__ import annotations

from pathlib import Path

import yaml


def test_canonical_browsing_pool_uses_active_stability_probe(repo_root: Path) -> None:
    policies = yaml.safe_load((repo_root / "policies.yaml").read_text(encoding="utf-8"))
    probe = policies["probes"]["browsing"]
    assert probe == {
        "url": "https://www.gstatic.com/generate_204",
        "method": "HEAD",
        "expected_status": "204",
        "interval": 180,
        "timeout": 3000,
        "lazy": False,
        "tolerance": 150,
    }

    pools = {item["id"]: item for item in policies["pools"]}
    assert pools["browsing"]["probe"] == "browsing"
    assert pools["general"]["probe"] == "connectivity"
