from __future__ import annotations

import hashlib
from types import SimpleNamespace

from clash_relay import slo_application
from clash_relay.operational_slo import ProductionOutcome, build_slo_attempt


def test_operational_slo_persists_to_independent_private_key(monkeypatch) -> None:
    storage: dict[str, bytes] = {}

    class FakePublisher:
        def __init__(self, *, token, account_id, namespace_title, key_name):
            assert token == "token"
            assert account_id == "account"
            assert namespace_title == "namespace"
            self.key_name = key_name

        def read(self):
            return storage.get(self.key_name)

        def publish(self, *, content):
            storage[self.key_name] = content
            return {
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }

    monkeypatch.setattr(slo_application, "CloudflareKVPublisher", FakePublisher)
    project = SimpleNamespace(
        config={"publishing": {"cloudflare_kv": {"key": "production-config"}}}
    )
    env = {
        "CLOUDFLARE_API_TOKEN": "token",
        "CLOUDFLARE_ACCOUNT_ID": "account",
        "CLOUDFLARE_KV_NAMESPACE_TITLE": "namespace",
    }
    attempt = build_slo_attempt(
        outcome=ProductionOutcome.PASSED,
        duration_ms=100.0,
        candidate_sha256="a" * 64,
        candidate_bytes=1234,
        promotion_guard_checked=True,
        epoch=1,
    )

    result = slo_application.persist_operational_slo(
        project=project,  # type: ignore[arg-type]
        attempt=attempt,
        env=env,
    )

    assert result["status"] == "published"
    assert result["attempts"] == 1
    assert "production-config.operational-slo-v1" in storage
    assert "production-config" not in storage
    serialized = storage["production-config.operational-slo-v1"]
    assert b"token" not in serialized
    assert b"account" not in serialized
    assert b"namespace" not in serialized
