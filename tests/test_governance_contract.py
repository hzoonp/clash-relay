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
            "job": "Validated SHA",
            "check_context": "Validated SHA",
        },
    ]

    ci = (repo_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert ci.startswith("name: CI\n")
    assert "workflow_call:" in ci
    assert "name: Python 3.12 quality" in ci
    assert "name: Verify Routing V2 drift" in ci
    assert "name: Validated SHA" in ci

    ci_check = contract["required_status_checks"][0]
    assert ci_check["check_context"] == ci_check["job"]
