from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import clash_relay.production_failure_metrics as failure_metrics
from clash_relay.production_failure_metrics import persist_failure_diagnostic
from clash_relay.production_metrics import (
    append_failure_metric,
    append_metrics_run,
    build_metrics_run,
    empty_metrics,
    metrics_summary,
    parse_metrics_bytes,
)


def _browsing() -> dict[str, object]:
    return {
        "diagnostics": {
            "tested_nodes": 4,
            "qualified_nodes": 3,
            "failed_nodes": 1,
        },
        "stable_nodes": 3,
        "reserve_nodes": 0,
        "scheduler_history": {},
    }


def _ai() -> dict[str, object]:
    return {
        "diagnostics": {"tested_nodes": 4, "probes": {}},
        "qualification_cache": {},
    }


def test_legacy_metrics_state_loads_with_empty_failure_history() -> None:
    state, status = parse_metrics_bytes(b'{"version":1,"runs":[]}')

    assert status == "loaded"
    assert state == {"version": 1, "runs": [], "failures": []}


def test_failure_metric_keeps_only_sanitized_aggregate_fields() -> None:
    state = append_failure_metric(
        empty_metrics(),
        {
            "status": "failed",
            "category": "subscription_fetch",
            "retryable": True,
            "qualification_failure_category": "transient",
            "url": "https://secret.example/subscription",
            "server": "192.0.2.10",
            "token": "do-not-leak",
            "message": "private exception text",
        },
        epoch=100,
    )

    assert state["failures"] == [
        {
            "epoch": 100,
            "category": "subscription_fetch",
            "retryable": True,
            "qualification_failure_category": "transient",
        }
    ]
    encoded = json.dumps(state, sort_keys=True)
    assert "secret.example" not in encoded
    assert "192.0.2.10" not in encoded
    assert "do-not-leak" not in encoded
    assert "private exception text" not in encoded


def test_failure_metric_rejects_unknown_category() -> None:
    with pytest.raises(ValueError, match="invalid aggregate production failure metric"):
        append_failure_metric(
            empty_metrics(),
            {"status": "failed", "category": "future-private-category"},
            epoch=1,
        )


def test_failure_ring_and_summary_are_bounded() -> None:
    state = empty_metrics()
    for index in range(65):
        state = append_failure_metric(
            state,
            {
                "status": "failed",
                "category": "subscription_fetch" if index % 2 else "candidate_validation",
                "retryable": index % 3 == 0,
            },
            epoch=1000 + index,
        )

    assert len(state["failures"]) == 60
    assert state["failures"][0]["epoch"] == 1005
    summary = metrics_summary(state)
    assert summary["runs"] == 0
    assert summary["failure_runs"] == 60
    assert summary["failure_categories"] == {
        "candidate_validation": 30,
        "subscription_fetch": 30,
    }
    assert summary["latest_failure_category"] == "candidate_validation"
    assert summary["recent_failure_rate"] == 1.0
    assert summary["recent_failure_streak"] == 60


def test_successful_run_preserves_failure_history_and_breaks_failure_streak(
    tmp_path: Path,
) -> None:
    state = append_failure_metric(
        empty_metrics(),
        {"status": "failed", "category": "configuration"},
        epoch=10,
    )
    candidate = tmp_path / "config.yaml"
    candidate.write_text("candidate\n", encoding="utf-8")
    run = build_metrics_run(
        candidate_path=candidate,
        browsing=_browsing(),
        ai=_ai(),
        epoch=20,
    )

    state = append_metrics_run(state, run)
    summary = metrics_summary(state)

    assert len(state["failures"]) == 1
    assert summary["failure_runs"] == 1
    assert summary["recent_failure_rate"] == 0.5
    assert summary["recent_failure_streak"] == 0


def test_persist_failure_diagnostic_reuses_private_metrics_key_without_leaks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for name in ("config.yaml", "subscriptions.yaml", "policies.yaml"):
        (tmp_path / name).write_text("placeholder\n", encoding="utf-8")

    monkeypatch.setattr(
        failure_metrics,
        "load_project",
        lambda **kwargs: SimpleNamespace(
            config={"publishing": {"cloudflare_kv": {"key": "production-config"}}}
        ),
    )
    seen: dict[str, object] = {}

    class FakePublisher:
        def __init__(
            self,
            *,
            token: str,
            account_id: str,
            namespace_title: str,
            key_name: str,
        ) -> None:
            seen.update(
                token=token,
                account_id=account_id,
                namespace_title=namespace_title,
                key_name=key_name,
            )

        def read(self) -> bytes:
            return b'{"version":1,"runs":[]}'

        def publish(self, *, content: bytes) -> dict[str, object]:
            seen["content"] = content
            return {"bytes": len(content), "sha256": "0" * 64}

    monkeypatch.setattr(failure_metrics, "CloudflareKVPublisher", FakePublisher)

    result = persist_failure_diagnostic(
        root=tmp_path,
        diagnostic={
            "status": "failed",
            "category": "cloudflare_publication",
            "url": "https://secret.example/private",
            "token": "super-secret",
        },
        env={
            "CLOUDFLARE_API_TOKEN": "token-value",
            "CLOUDFLARE_ACCOUNT_ID": "account",
            "CLOUDFLARE_KV_NAMESPACE_TITLE": "namespace",
        },
    )

    assert seen["key_name"] == "production-config.production-metrics-v1"
    payload = bytes(seen["content"])
    assert b"secret.example" not in payload
    assert b"super-secret" not in payload
    assert json.loads(payload)["failures"][-1]["category"] == "cloudflare_publication"
    assert result["status"] == "published"
    assert result["failure_runs"] == 1
    assert result["failure_categories"] == {"cloudflare_publication": 1}


def test_failure_persistence_skips_when_cloudflare_is_unavailable(tmp_path: Path) -> None:
    for name in ("config.yaml", "subscriptions.yaml", "policies.yaml"):
        (tmp_path / name).write_text("placeholder\n", encoding="utf-8")

    assert persist_failure_diagnostic(
        root=tmp_path,
        diagnostic={"status": "failed", "category": "configuration"},
        env={},
    ) == {"status": "skipped", "reason": "cloudflare_unavailable"}
