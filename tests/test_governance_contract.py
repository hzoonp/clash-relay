from __future__ import annotations

import json
from pathlib import Path


def test_main_governance_contract_matches_authoritative_workflows(repo_root: Path) -> None:
    contract = json.loads(
        (repo_root / ".github" / "main-governance.json").read_text(encoding="utf-8")
    )

    assert contract["version"] == 2
    assert contract["branch"] == "main"
    assert contract["desired_enforcement"] == "active"
    assert contract["pull_request"] == {
        "required": True,
        "required_approvals": 0,
        "require_code_owner_review": False,
        "require_last_push_approval": False,
    }
    assert contract["history"] == {
        "allow_force_push": False,
        "allow_deletion": False,
    }
    assert contract["required_status_checks"] == [
        {
            "workflow": "CI",
            "job": "Validate exact commit / Validated SHA",
            "check_context": "Validate exact commit / Validated SHA",
        },
        {
            "workflow": "CI",
            "job": "Verify finalized Routing V2 graph",
            "check_context": "Verify finalized Routing V2 graph",
        },
    ]

    ci = (repo_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert ci.startswith("name: CI\n")
    assert "workflow_call:" in ci
    assert "name: Python 3.12 quality" in ci
    assert "name: Verify Routing V2 drift" in ci
    assert "name: Verify finalized Routing V2 graph" in ci
    assert "name: Validate exact commit / Validated SHA" in ci

    for check in contract["required_status_checks"]:
        assert check["check_context"] == check["job"]
