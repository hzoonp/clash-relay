from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from clash_relay.errors import CommitUnknownError, PublicationError
from clash_relay.publishers.cloudflare_kv import CloudflareKVPublisher


class _Response:
    def __init__(self, document: object) -> None:
        self._payload = json.dumps(document).encode("utf-8")

    def read(self, size: int = -1) -> bytes:
        return self._payload if size < 0 else self._payload[:size]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def _namespace_success() -> _Response:
    return _Response(
        {
            "success": True,
            "errors": [],
            "messages": [],
            "result": [{"id": "1" * 32, "title": "clash-relay-config"}],
            "result_info": {"page": 1, "per_page": 100, "total_count": 1},
        }
    )


def _publisher(*, token: str = "private-token") -> CloudflareKVPublisher:
    return CloudflareKVPublisher(
        token=token,
        account_id="0" * 32,
        namespace_title="clash-relay-config",
        key_name="production-config",
    )


@pytest.mark.parametrize("status", [408, 425, 429, 500, 502, 503, 504])
def test_write_statuses_with_ambiguous_remote_outcome_become_commit_unknown(
    monkeypatch, status: int
) -> None:
    calls = 0

    def fake_urlopen(request, timeout):
        nonlocal calls
        assert timeout == 30
        calls += 1
        if request.get_method() == "GET":
            return _namespace_success()
        raise urllib.error.HTTPError(
            request.full_url,
            status,
            "injected write failure",
            {},
            io.BytesIO(b"{}"),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(CommitUnknownError, match=f"HTTP {status}"):
        _publisher().publish(content=b"candidate: safe\n")
    assert calls == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
def test_definite_client_rejections_remain_definite_publication_failures(
    monkeypatch, status: int
) -> None:
    def fake_urlopen(request, timeout):
        if request.get_method() == "GET":
            return _namespace_success()
        raise urllib.error.HTTPError(
            request.full_url,
            status,
            "injected client rejection",
            {},
            io.BytesIO(b"{}"),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(PublicationError, match=f"HTTP {status}") as captured:
        _publisher().publish(content=b"candidate: safe\n")
    assert not isinstance(captured.value, CommitUnknownError)


@pytest.mark.parametrize(
    "write_outcome",
    [
        urllib.error.URLError("connection reset after request body"),
        OSError("socket closed after request body"),
    ],
)
def test_transport_loss_after_write_request_is_commit_unknown(monkeypatch, write_outcome) -> None:
    def fake_urlopen(request, timeout):
        if request.get_method() == "GET":
            return _namespace_success()
        raise write_outcome

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(CommitUnknownError, match="response was not received"):
        _publisher().publish(content=b"candidate: safe\n")


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"[]",
    ],
)
def test_unverifiable_write_success_payload_is_commit_unknown(monkeypatch, payload: bytes) -> None:
    class _Raw:
        def read(self, size: int = -1) -> bytes:
            return payload if size < 0 else payload[:size]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    def fake_urlopen(request, timeout):
        if request.get_method() == "GET":
            return _namespace_success()
        return _Raw()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(CommitUnknownError, match="could not be verified"):
        _publisher().publish(content=b"candidate: safe\n")


def test_namespace_lookup_failure_happens_before_any_mutating_request(monkeypatch) -> None:
    methods: list[str] = []

    def fake_urlopen(request, timeout):
        methods.append(request.get_method())
        raise urllib.error.URLError("injected namespace lookup outage")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(PublicationError, match="request failed") as captured:
        _publisher().publish(content=b"candidate: safe\n")
    assert not isinstance(captured.value, CommitUnknownError)
    assert methods == ["GET"]


def test_failure_matrix_never_echoes_token_or_candidate_secret(monkeypatch) -> None:
    token = "DO-NOT-LEAK-CLOUDFLARE-TOKEN"
    candidate_secret = "DO-NOT-LEAK-NODE-PASSWORD"

    def fake_urlopen(request, timeout):
        if request.get_method() == "GET":
            return _namespace_success()
        raise urllib.error.HTTPError(
            request.full_url,
            503,
            f"upstream mentioned {token} {candidate_secret}",
            {},
            io.BytesIO(b"{}"),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(CommitUnknownError) as captured:
        _publisher(token=token).publish(content=f"password: {candidate_secret}\n".encode())
    message = str(captured.value)
    assert token not in message
    assert candidate_secret not in message
