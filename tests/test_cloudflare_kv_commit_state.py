from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request

import pytest

from clash_relay.errors import CommitUnknownError, PublicationError
from clash_relay.publishers.cloudflare_kv import CloudflareKVPublisher


class _Response:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, size: int = -1) -> bytes:
        return self._payload if size < 0 else self._payload[:size]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def _json_response(document: dict) -> _Response:
    return _Response(json.dumps(document).encode("utf-8"))


def _namespace() -> dict:
    return {
        "success": True,
        "errors": [],
        "messages": [],
        "result": [{"id": "1" * 32, "title": "clash-relay-config"}],
        "result_info": {"page": 1, "per_page": 100, "total_count": 1},
    }


def _publisher() -> CloudflareKVPublisher:
    return CloudflareKVPublisher(
        token="private-api-token",
        account_id="0" * 32,
        namespace_title="clash-relay-config",
        key_name="production-config",
    )


def test_put_transport_failure_is_commit_unknown(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        if request.get_method() == "GET":
            return _json_response(_namespace())
        raise urllib.error.URLError("response lost")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(CommitUnknownError):
        _publisher().publish(content=b"candidate\n")


@pytest.mark.parametrize("status", [408, 425, 429, 500, 503])
def test_put_retryable_or_server_http_outcome_is_commit_unknown(
    monkeypatch,
    status: int,
) -> None:
    def fake_urlopen(request, timeout):
        if request.get_method() == "GET":
            return _json_response(_namespace())
        raise urllib.error.HTTPError(request.full_url, status, "Ambiguous", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(CommitUnknownError):
        _publisher().publish(content=b"candidate\n")


def test_put_client_rejection_is_definite_publication_error(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        if request.get_method() == "GET":
            return _json_response(_namespace())
        raise urllib.error.HTTPError(request.full_url, 400, "Rejected", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(PublicationError) as captured:
        _publisher().publish(content=b"candidate\n")

    assert not isinstance(captured.value, CommitUnknownError)


def test_put_unparseable_success_response_is_commit_unknown(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        if request.get_method() == "GET":
            return _json_response(_namespace())
        return _Response(b"not-json")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(CommitUnknownError):
        _publisher().publish(content=b"candidate\n")


class _TruncatedResponse(_Response):
    def read(self, size: int = -1) -> bytes:
        raise http.client.IncompleteRead(b'{"success":true', 20)


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_truncated_write_response_preserves_unknown_commit_state(monkeypatch, method: str) -> None:
    def fake_urlopen(request, timeout):
        if request.get_method() == "GET":
            return _json_response(_namespace())
        assert request.get_method() == method
        return _TruncatedResponse(b"")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    publisher = _publisher()
    with pytest.raises(CommitUnknownError, match="response was not received"):
        if method == "PUT":
            publisher.publish(content=b"candidate\n")
        else:
            publisher.delete()


@pytest.mark.parametrize("failure_point", ["namespace", "value"])
def test_truncated_read_response_is_publication_error(monkeypatch, failure_point: str) -> None:
    def fake_urlopen(request, timeout):
        if "/storage/kv/namespaces?" in request.full_url and failure_point == "value":
            return _json_response(_namespace())
        return _TruncatedResponse(b"")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(PublicationError, match="request failed") as captured:
        _publisher().read()
    assert not isinstance(captured.value, CommitUnknownError)
