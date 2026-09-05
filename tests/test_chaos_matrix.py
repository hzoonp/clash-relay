from __future__ import annotations

import urllib.request
from pathlib import Path

import pytest

from clash_relay import fetch
from clash_relay.errors import FetchError, SubscriptionError
from clash_relay.production_metrics import empty_metrics, parse_metrics_bytes
from clash_relay.subscription_parser import parse_subscription


def test_subscription_timeout_fails_without_echoing_secret_url(monkeypatch) -> None:
    secret_url = "https://example.com/subscription/SUPER-SECRET-TOKEN"

    monkeypatch.setattr(fetch, "_validate_resolved_destination", lambda url: None)

    class _Opener:
        def open(self, request, timeout):
            raise TimeoutError(secret_url)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *args, **kwargs: _Opener())

    with pytest.raises(FetchError) as caught:
        fetch.fetch_subscription(
            secret_url,
            timeout=1,
            max_bytes=1024,
            allow_http=False,
            allow_file=False,
        )

    message = str(caught.value)
    assert "SUPER-SECRET-TOKEN" not in message
    assert "subscription fetch failed" in message


def test_html_subscription_is_rejected_as_untrusted_payload() -> None:
    with pytest.raises(SubscriptionError):
        parse_subscription("<html><body>upstream error</body></html>")


def test_empty_subscription_never_synthesizes_nodes() -> None:
    parsed = parse_subscription("\n\t\n")

    assert parsed.proxies == ()
    assert parsed.skipped_items == 0


def test_corrupt_production_metrics_falls_back_to_empty_bounded_state() -> None:
    state, status = parse_metrics_bytes(b"not-json-with-secret=https://private.example/sub")

    assert status == "invalid"
    assert state == empty_metrics()
    assert "private.example" not in repr(state)


def test_publish_workflow_serializes_production_mutations(repo_root: Path) -> None:
    workflow = (repo_root / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")

    assert "group: clash-relay-publish-${{ github.ref }}" in workflow
    assert "cancel-in-progress: false" in workflow
    assert workflow.count("python scripts/run_production_release.py") == 1


def test_post_commit_observability_is_not_a_publication_gate(repo_root: Path) -> None:
    lifecycle = (repo_root / "src" / "clash_relay" / "production_lifecycle.py").read_text(
        encoding="utf-8"
    )

    release_stage = lifecycle.index(
        "release_stage = self._release_candidate_stage(project, binary)"
    )
    proof = lifecycle.index("proof = self._post_commit_proof(release=release)")
    metrics = lifecycle.index("metrics = self._persist_production_metrics(project)")
    assert release_stage < proof < metrics
    assert 'self.warnings.append("render_production_proof")' in lifecycle
    assert 'self.warnings.append("render_release_manifest")' in lifecycle
    assert '"persist_production_metrics",' in lifecycle

    metrics_start = lifecycle.index("    def _persist_production_metrics(")
    metrics_end = lifecycle.index("    def _render_existing_proof", metrics_start)
    metrics_body = lifecycle[metrics_start:metrics_end]
    assert "self._best_effort_state(" in metrics_body
