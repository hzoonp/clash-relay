from __future__ import annotations

from pathlib import Path

import pytest

from clash_relay.errors import ValidationError
from clash_relay.publication_decision import PublicationMode, resolve_publication_decision

ROOT = Path(__file__).resolve().parents[1]


def test_automatic_github_events_are_always_dry_run() -> None:
    for event_name in ("push", "schedule"):
        decision = resolve_publication_decision(event_name=event_name)
        assert decision.mode is PublicationMode.DRY_RUN
        assert decision.reason == "automatic_event"
        assert decision.should_publish is False

        overridden = resolve_publication_decision(
            explicit_publish=True,
            event_name=event_name,
            manual_publish=True,
        )
        assert overridden.mode is PublicationMode.DRY_RUN
        assert overridden.should_publish is False


def test_manual_dispatch_requires_explicit_publication_enablement() -> None:
    assert (
        resolve_publication_decision(
            event_name="workflow_dispatch",
            manual_publish=False,
        ).should_publish
        is False
    )
    assert (
        resolve_publication_decision(
            event_name="workflow_dispatch",
            manual_publish="false",
        ).should_publish
        is False
    )
    assert (
        resolve_publication_decision(
            event_name="workflow_dispatch",
            manual_publish=True,
        ).should_publish
        is True
    )
    assert (
        resolve_publication_decision(
            event_name="workflow_dispatch",
            manual_publish=" TRUE ",
        ).should_publish
        is True
    )


def test_local_explicit_publication_remains_available() -> None:
    assert resolve_publication_decision(explicit_publish=True).should_publish is True
    assert resolve_publication_decision(explicit_publish=False).should_publish is False


def test_unsupported_implicit_event_fails_closed() -> None:
    with pytest.raises(ValidationError, match="unsupported"):
        resolve_publication_decision(event_name="pull_request")


def test_explicit_local_flag_keeps_preexisting_override_semantics() -> None:
    decision = resolve_publication_decision(
        explicit_publish=True,
        event_name="local_operator",
    )
    assert decision.should_publish is True
    assert decision.reason == "explicit_flag"


def test_canonical_runner_uses_typed_publication_decision() -> None:
    runner = (ROOT / "scripts/run_production_release.py").read_text(encoding="utf-8")
    assert "from clash_relay.publication_decision import resolve_publication_decision" in runner
    assert "decision = resolve_publication_decision(" in runner
    assert "publish = decision.should_publish" in runner
    assert "resolve_cutover_publication_mode" not in runner


def test_legacy_publication_resolver_is_absent_from_runtime_code() -> None:
    legacy_token = "resolve_publication_mode"
    for base in (ROOT / "src/clash_relay", ROOT / "scripts"):
        for path in sorted(base.glob("*.py")):
            assert legacy_token not in path.read_text(encoding="utf-8"), path
