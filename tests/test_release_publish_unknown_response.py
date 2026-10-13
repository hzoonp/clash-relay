"""End-to-end classification of malformed Cloudflare write responses.

These tests drive the real ``CloudflareKVPublisher`` through the release bundle
with an in-memory Cloudflare KV double. They prove that a write the server
accepted but whose response could not be verified (for example the malformed
``{}`` body) stays commit-unknown and does not trigger compensating writes, even
when the read-back is still serving the previous value.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

from clash_relay.errors import CommitUnknownError, PublicationError
from clash_relay.publishers.cloudflare_kv import CloudflareKVPublisher
from clash_relay.release_bundle import (
    publish_release_bundle,
    release_keys,
)
from clash_relay.release_transaction import parse_release_transaction


class _Response:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, size: int = -1) -> bytes:
        return self._payload if size < 0 else self._payload[:size]

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class CloudflareBackend:
    """Minimal Cloudflare KV that can return a malformed write acknowledgement."""

    def __init__(self) -> None:
        self.namespace_id = "1" * 32
        self.values: dict[str, bytes] = {}
        self.stale_reads: dict[str, list[object]] = {}
        self.malformed_write_response: set[str] = set()
        self.methods: list[str] = []
        self.production_writes: list[bytes] = []

    def _namespace_document(self) -> dict:
        return {
            "success": True,
            "errors": [],
            "messages": [],
            "result": [{"id": self.namespace_id, "title": "clash-relay-config"}],
            "result_info": {"page": 1, "per_page": 100, "total_count": 1},
        }

    @staticmethod
    def _multipart_value(body: bytes) -> bytes:
        marker = b"\r\n\r\n"
        start = body.index(marker) + len(marker)
        end = body.rindex(b"\r\n--")
        return body[start:end]

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_urlopen(request, timeout):
            self.methods.append(request.get_method())
            url = request.full_url
            if "/storage/kv/namespaces?" in url:
                return _Response(json.dumps(self._namespace_document()).encode())
            key = urllib.parse.unquote(url.split("/values/", 1)[1])
            if request.get_method() == "GET":
                stale = self.stale_reads.get(key)
                if stale is not None and int(stale[0]) > 0:
                    stale[0] = int(stale[0]) - 1
                    value = stale[1]
                    if not isinstance(value, bytes):
                        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
                    return _Response(value)
                value = self.values.get(key)
                if value is None:
                    raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
                return _Response(value)
            if request.get_method() == "PUT":
                payload = self._multipart_value(request.data)
                self.values[key] = payload
                if key == "production-config":
                    self.production_writes.append(payload)
                if key in self.malformed_write_response:
                    self.malformed_write_response.discard(key)
                    return _Response(b"{}")
                return _Response(b'{"success": true, "result": {}}')
            if request.get_method() == "DELETE":
                self.values.pop(key, None)
                return _Response(b'{"success": true, "result": null}')
            raise AssertionError(f"unexpected method {request.get_method()}")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    def factory(self, key: str) -> CloudflareKVPublisher:
        return CloudflareKVPublisher(
            token="private-api-token",
            account_id="0" * 32,
            namespace_title="clash-relay-config",
            key_name=key,
            namespace_id=self.namespace_id,
        )


def test_malformed_write_response_with_stale_readback_preserves_unknown_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("clash_relay.release_bundle._READ_BACK_DELAYS", (0.0,))
    backend = CloudflareBackend()
    backend.install(monkeypatch)
    key = "production-config"
    keys = release_keys(key)
    first, second = b"first\n", b"second\n"
    publish_release_bundle(factory=backend.factory, production_key=key, content=first)

    backend.malformed_write_response.add(key)
    backend.stale_reads[key] = [10, first]

    with pytest.raises(CommitUnknownError) as captured:
        publish_release_bundle(factory=backend.factory, production_key=key, content=second)

    assert captured.value.production_changed == "unknown"
    # The server accepted the PUT, but no compensating write followed.
    assert backend.values[key] == second
    assert backend.production_writes[-1] == second
    # The durable intent survives for a later read-only reconciliation.
    transaction = parse_release_transaction(backend.values[keys.transaction])
    assert transaction is not None
    assert transaction.phase == "prepare"


def test_definite_write_rejection_runs_verified_compensation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("clash_relay.release_bundle._READ_BACK_DELAYS", (0.0,))
    backend = CloudflareBackend()
    backend.install(monkeypatch)
    key = "production-config"
    keys = release_keys(key)
    first = b"first\n"
    publish_release_bundle(factory=backend.factory, production_key=key, content=first)

    original = urllib.request.urlopen

    def rejecting_urlopen(request, timeout):
        if request.get_method() == "PUT" and request.full_url.endswith("/values/production-config"):
            raise urllib.error.HTTPError(request.full_url, 403, "rejected", {}, None)
        return original(request, timeout)

    monkeypatch.setattr(urllib.request, "urlopen", rejecting_urlopen)

    with pytest.raises(
        PublicationError, match="previous production bytes were restored"
    ) as captured:
        publish_release_bundle(factory=backend.factory, production_key=key, content=b"second\n")

    assert not isinstance(captured.value, CommitUnknownError)
    assert backend.values[key] == first
    assert parse_release_transaction(backend.values[keys.transaction]) is None
