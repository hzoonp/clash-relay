def _text(relative: str) -> str:
    with open(relative, encoding="utf-8") as handle:
        return handle.read()


def _pull_request_block(workflow: str) -> str:
    marker = "  pull_request:"
    assert marker in workflow
    tail = workflow.split(marker, 1)[1]
    for next_trigger in ("\n  workflow_dispatch:", "\n  workflow_call:"):
        if next_trigger in tail:
            return tail.split(next_trigger, 1)[0]
    return tail


def test_required_check_contexts_have_unconditional_pull_request_producers() -> None:
    contract = _text(".github/main-governance.json")
    assert '"check_context": "Validate exact commit / Validated SHA"' in contract
    assert '"check_context": "Verify finalized Routing V2 graph"' in contract

    ci = _text(".github/workflows/ci.yml")
    assert ci.startswith("name: CI\n")
    assert "name: Validate exact commit / Validated SHA" in ci
    assert "name: Verify finalized Routing V2 graph" in ci
    assert "Verify Routing V2 drift" in ci
    assert "python scripts/routing_shadow.py" in ci
    assert "paths:" not in _pull_request_block(ci)
