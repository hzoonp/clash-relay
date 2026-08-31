from __future__ import annotations

import json

from clash_relay.ai_qualification_cache import (
    ai_runtime_fingerprints,
    cached_service_decisions,
    derive_ai_cache_key,
    empty_ai_cache,
    parse_ai_cache_bytes,
    update_ai_cache_service,
)


def _candidate(password: str = "secret") -> dict:
    return {
        "proxy-providers": {
            "cr_ai_us": {
                "type": "inline",
                "payload": [
                    {
                        "name": "runtime-secret-name",
                        "type": "ss",
                        "server": "198.51.100.10",
                        "port": 443,
                        "cipher": "aes-128-gcm",
                        "password": password,
                    }
                ],
            }
        }
    }


def test_ai_cache_fingerprint_is_opaque_and_changes_with_proxy_payload() -> None:
    key = derive_ai_cache_key("private-token")
    first = ai_runtime_fingerprints(_candidate("one"), key)
    second = ai_runtime_fingerprints(_candidate("two"), key)
    fingerprint = first["runtime-secret-name"]

    assert len(fingerprint) == 64
    assert fingerprint != second["runtime-secret-name"]
    assert "runtime-secret-name" not in fingerprint
    assert "198.51.100.10" not in fingerprint
    assert "one" not in fingerprint


def test_invalid_ai_cache_safely_degrades_to_empty() -> None:
    cache, status = parse_ai_cache_bytes(b'{"version":999,"nodes":{"SECRET":{}}}')
    assert status == "invalid"
    assert cache == empty_ai_cache()
    assert "SECRET" not in json.dumps(cache)


def test_fresh_pass_and_failure_are_reused_with_different_ttls() -> None:
    key = derive_ai_cache_key("token")
    fingerprints = ai_runtime_fingerprints(_candidate(), key)
    fingerprint = fingerprints["runtime-secret-name"]
    cache = {
        "version": 1,
        "nodes": {
            fingerprint: {
                "services": {
                    "ai_openai": {"passed": True, "checked_epoch": 1000},
                    "ai_claude": {"passed": False, "checked_epoch": 1000},
                }
            }
        },
    }

    passed, failed, live = cached_service_decisions(
        cache, fingerprints, "ai_openai", now_epoch=1000 + 5 * 60 * 60
    )
    assert passed == {"runtime-secret-name"}
    assert failed == set()
    assert live == set()

    passed, failed, live = cached_service_decisions(
        cache, fingerprints, "ai_claude", now_epoch=1000 + 30 * 60
    )
    assert passed == set()
    assert failed == {"runtime-secret-name"}
    assert live == set()

    passed, failed, live = cached_service_decisions(
        cache, fingerprints, "ai_claude", now_epoch=1000 + 2 * 60 * 60
    )
    assert passed == set()
    assert failed == set()
    assert live == {"runtime-secret-name"}


def test_ai_cache_update_contains_no_runtime_or_proxy_secrets() -> None:
    key = derive_ai_cache_key("token")
    fingerprints = ai_runtime_fingerprints(_candidate(), key)
    cache = update_ai_cache_service(
        empty_ai_cache(),
        fingerprints,
        "ai_gemini",
        checked_names={"runtime-secret-name"},
        passed_names={"runtime-secret-name"},
        now_epoch=1234,
    )
    serialized = json.dumps(cache, sort_keys=True)

    assert "runtime-secret-name" not in serialized
    assert "198.51.100.10" not in serialized
    assert "secret" not in serialized
    record = next(iter(cache["nodes"].values()))
    assert record["services"]["ai_gemini"] == {"passed": True, "checked_epoch": 1234}
