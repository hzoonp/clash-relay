from __future__ import annotations

from pathlib import Path

from clash_relay.policy_document import load_policy_document


def test_canonical_browsing_pool_uses_active_stability_probe(repo_root: Path) -> None:
    policies = load_policy_document(repo_root / "policies.yaml").document
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
