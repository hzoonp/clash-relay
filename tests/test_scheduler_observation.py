from __future__ import annotations

import json
from types import SimpleNamespace

import clash_relay.scheduler_observation as observation
from clash_relay.errors import PublicationError


def _project():
    return SimpleNamespace(
        config={"publishing": {"cloudflare_kv": {"key": "production-config"}}}
    )


def _env() -> dict[str, str]:
    return {
        "CLOUDFLARE_API_TOKEN": "PRIVATE-TOKEN",
        "CLOUDFLARE_ACCOUNT_ID": "PRIVATE-ACCOUNT",
        "CLOUDFLARE_KV_NAMESPACE_TITLE": "PRIVATE-NAMESPACE",
    }


def test_scheduler_observation_publishes_only_compiled_aggregate_evidence(monkeypatch) -> None:
    writes: list[tuple[str, bytes]] = []

    class FakePublisher:
        def __init__(self, *, token: str, account_id: str, namespace_title: str, key_name: str):
            assert token == "PRIVATE-TOKEN"
            assert account_id == "PRIVATE-ACCOUNT"
            assert namespace_title == "PRIVATE-NAMESPACE"
            self.key_name = key_name

        def read(self) -> bytes:
            assert self.key_name == "production-config.production-metrics-v1"
            return b"PRIVATE METRICS BYTES"

        def publish(self, *, content: bytes):
            writes.append((self.key_name, content))
            return {"bytes": len(content), "sha256": "a" * 64}

    state = {
        "version": 1,
        "runs": [
            {
                "epoch": index,
                "browsing": {
                    "qualified": 4,
                    "stable": 3,
                    "historically_demoted": 1,
                    "regions": {"US": {"stable": 2}, "JP": {"stable": 1}},
                },
                "ai": {"qualified_by_service": {"openai": 2, "claude": 1, "gemini": 1}},
                "qualification": {"browsing_attempts": 2, "recovered_by_retry": index == 3},
                "promotion_guard": {"status": "passed"},
            }
            for index in (1, 2, 3)
        ],
        "failures": [],
    }
    monkeypatch.setattr(observation, "CloudflareKVPublisher", FakePublisher)
    monkeypatch.setattr(observation, "parse_metrics_bytes", lambda _content: (state, "loaded"))

    result = observation.publish_scheduler_observation(project=_project(), env=_env())

    assert result["status"] == "published"
    assert result["mode"] == "observe_only"
    assert result["privacy"] == "aggregate_only"
    assert result["evidence_status"] == "ready"
    assert result["sample_runs"] == 3
    assert writes[0][0] == "production-config.scheduler-evidence-v1"
    payload = json.loads(writes[0][1])
    serialized = json.dumps(payload, sort_keys=True)
    assert payload["browsing"]["stable_regions"] == ["JP", "US"]
    assert payload["services"]["covered_service_count"] == 3
    assert "PRIVATE" not in serialized
    assert "server" not in serialized.lower()
    assert "url" not in serialized.lower()


def test_scheduler_observation_skips_without_cloudflare_credentials() -> None:
    assert observation.publish_scheduler_observation(project=_project(), env={}) == {
        "status": "skipped",
        "reason": "cloudflare_unavailable",
    }


def test_scheduler_observation_is_best_effort_on_metrics_transport_failure(monkeypatch) -> None:
    class FailingPublisher:
        def __init__(self, **_kwargs):
            pass

        def read(self) -> bytes:
            raise PublicationError("PRIVATE transport details")

    monkeypatch.setattr(observation, "CloudflareKVPublisher", FailingPublisher)

    result = observation.publish_scheduler_observation(project=_project(), env=_env())

    assert result == {"status": "unavailable", "reason": "metrics_read_failed"}
    assert "PRIVATE" not in json.dumps(result)
