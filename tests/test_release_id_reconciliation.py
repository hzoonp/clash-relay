from __future__ import annotations

from types import SimpleNamespace

from clash_relay.production_application import reconcile_production_release_ids
from clash_relay.release_bundle import manifest_bytes, release_id_for, release_keys


def test_release_id_reconciliation_uses_verified_private_kv_objects(monkeypatch) -> None:
    candidate = b"candidate\n"
    previous = b"previous\n"
    candidate_id = release_id_for(candidate)
    previous_id = release_id_for(previous)
    keys = release_keys("production-config")
    values = {
        keys.config(candidate_id): candidate,
        keys.manifest(candidate_id): manifest_bytes(candidate),
        keys.config(previous_id): previous,
        keys.manifest(previous_id): manifest_bytes(previous),
        keys.production: candidate,
        keys.current_pointer: (candidate_id + "\n").encode(),
        keys.previous_pointer: (previous_id + "\n").encode(),
    }

    class _Publisher:
        def __init__(self, key: str) -> None:
            self.key = key

        def resolve_namespace_id(self) -> str:
            return "namespace"

        def read(self) -> bytes | None:
            return values.get(self.key)

        def publish(self, *, content: bytes) -> None:
            raise AssertionError("reconciliation must be read-only")

    monkeypatch.setattr(
        "clash_relay.production_application._publisher",
        lambda **kwargs: _Publisher(kwargs["key_name"]),
    )
    project = SimpleNamespace(config={"publishing": {"cloudflare_kv": {"key": keys.production}}})
    result = reconcile_production_release_ids(
        project=project,
        candidate_release_id=candidate_id,
        previous_release_id=previous_id,
        env={
            "CLOUDFLARE_API_TOKEN": "token",
            "CLOUDFLARE_ACCOUNT_ID": "account",
            "CLOUDFLARE_KV_NAMESPACE_TITLE": "namespace",
        },
    )
    assert result["status"] == "committed"
