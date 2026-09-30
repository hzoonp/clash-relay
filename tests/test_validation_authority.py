from __future__ import annotations

from clash_relay.validation_authority import select_trusted_validation


def _run(**updates):
    value = {
        "id": 42,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "head_branch": "main",
        "head_sha": "a" * 40,
        "path": ".github/workflows/publish.yml",
    }
    value.update(updates)
    return value


def test_exact_successful_main_push_is_trusted() -> None:
    result = select_trusted_validation([_run()], sha="a" * 40)
    assert result is not None
    assert result.run_id == 42
    assert result.head_sha == "a" * 40


def test_validation_authority_fails_closed_on_mismatch() -> None:
    mismatches = [
        _run(event="schedule"),
        _run(status="in_progress"),
        _run(conclusion="failure"),
        _run(head_branch="feature"),
        _run(head_sha="b" * 40),
        _run(path=".github/workflows/ci.yml"),
        _run(id=True),
    ]
    assert select_trusted_validation(mismatches, sha="a" * 40) is None


def test_validation_authority_skips_malformed_entries() -> None:
    result = select_trusted_validation(
        [None, "bad", {"id": 1}, _run(id=99)],
        sha="a" * 40,
    )
    assert result is not None
    assert result.run_id == 99
