import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _pull_request_block(workflow: str) -> str:
    marker = "  pull_request:"
    assert marker in workflow
    tail = workflow.split(marker, 1)[1]
    for next_trigger in ("\n  push:", "\n  workflow_dispatch:"):
        if next_trigger in tail:
            return tail.split(next_trigger, 1)[0]
    return tail


def test_required_check_contexts_have_unconditional_pull_request_producers() -> None:
    contract = json.loads(_text(".github/main-governance.json"))
    contexts = {item["check_context"] for item in contract["required_status_checks"]}
    assert contexts == {
        "Validate exact commit / Validated SHA",
        "Verify finalized Routing V2 graph",
    }

    ci = _text(".github/workflows/ci.yml")
    validate = _text(".github/workflows/validate.yml")
    routing = _text(".github/workflows/routing-shadow.yml")

    assert "name: Validate exact commit" in ci
    assert "name: Validated SHA" in validate
    assert "paths:" not in _pull_request_block(ci)

    assert "name: Routing V2 Drift Guard" in routing
    assert "name: Verify finalized Routing V2 graph" in routing
    assert "paths:" not in _pull_request_block(routing)
