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
            "check_context": "Validate exact commit / Validated SHA",
        },
        {
            "workflow": "Routing V2 Drift Guard",
            "job": "Verify finalized Routing V2 graph",
            "check_context": "Verify finalized Routing V2 graph",
        },
    ]

    ci = (repo_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    validate = (repo_root / ".github" / "workflows" / "validate.yml").read_text(encoding="utf-8")
    routing = (repo_root / ".github" / "workflows" / "routing-shadow.yml").read_text(
        encoding="utf-8"
    )

    assert ci.startswith("name: CI\n")
    assert "    name: Validate exact commit\n" in ci
    assert "    name: Validated SHA\n" in validate
    assert routing.startswith("name: Routing V2 Drift Guard\n")
    assert "    name: Verify finalized Routing V2 graph\n" in routing

    ci_check = contract["required_status_checks"][0]
    assert ci_check["check_context"] == f"Validate exact commit / {ci_check['job']}"
    routing_check = contract["required_status_checks"][1]
    assert routing_check["check_context"] == routing_check["job"]
