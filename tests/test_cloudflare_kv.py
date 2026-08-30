from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request

import pytest

from clash_relay.errors import PublicationError
from clash_relay.publishers.cloudflare_kv import CloudflareKVPublisher


class _Response:
    def __init__(self, document: dict) -> None:
        self._payload = json.dumps(document).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def _success(result, *, total_count: int | None = None) -> dict:
    document = {"success": True, "errors": [], "messages": [], "result": result}
    if total_count is not None:
        document["result_info"] = {"page": 1, "per_page": 100, "total_count": total_count}
    return document


def test_cloudflare_publisher_resolves_namespace_and_writes_exact_config(monkeypatch) -> None:
    requests: list[urllib.request.Request] = []

    def fake_urlopen(request, timeout):
        assert timeout == 30
        requests.append(request)
        if request.get_method() == "GET":
            return _Response(
                _success(
                    [{"id": "1" * 32, "title": "clash-relay-config"}],
                    total_count=1,
                )
            )
        assert request.get_method() == "PUT"
        return _Response(_success({}))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    content = b"mixed-port: 7890\nmode: rule\n"
    result = CloudflareKVPublisher(
        token="private-api-token",
        account_id="0" * 32,
        namespace_title="clash-relay-config",
        key_name="production-config",
    ).publish(content=content)

    assert len(requests) == 2
    assert requests[0].get_method() == "GET"
    assert "/storage/kv/namespaces?" in requests[0].full_url
    assert requests[0].get_header("Authorization") == "Bearer private-api-token"
    assert requests[1].get_method() == "PUT"
    assert requests[1].full_url.endswith("/values/production-config")
    assert requests[1].data is not None
    assert content in requests[1].data
    assert result == {
        "backend": "cloudflare_kv",
        "namespace_title": "clash-relay-config",
        "key": "production-config",
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def test_cloudflare_publisher_fails_closed_when_namespace_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout: _Response(_success([], total_count=0)),
    )
    publisher = CloudflareKVPublisher(
        token="private-api-token",
        account_id="0" * 32,
        namespace_title="missing-namespace",
    )
    with pytest.raises(PublicationError, match="exactly one namespace"):
        publisher.publish(content=b"valid: yaml\n")


def test_cloudflare_api_failures_do_not_echo_token_or_candidate(monkeypatch) -> None:
    def forbidden(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    token = "do-not-print-this-token"
    candidate = b"password: do-not-print-this-node-password\n"
    publisher = CloudflareKVPublisher(
        token=token,
        account_id="0" * 32,
        namespace_title="clash-relay-config",
    )
    with pytest.raises(PublicationError) as captured:
        publisher.publish(content=candidate)
    message = str(captured.value)
    assert "HTTP 403" in message
    assert token not in message
    assert "do-not-print-this-node-password" not in message


@pytest.mark.parametrize("key", ["", ".", "..", "has whitespace"])
def test_cloudflare_publisher_rejects_unsafe_key_names(key: str) -> None:
    with pytest.raises(PublicationError):
        CloudflareKVPublisher(
            token="token",
            account_id="0" * 32,
            namespace_title="clash-relay-config",
            key_name=key,
        )
