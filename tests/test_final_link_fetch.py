from __future__ import annotations

import gzip
import io
import ssl
import urllib.request

import pytest

from clash_relay import fetch
from clash_relay.errors import FetchError


def _transport(monkeypatch, body, *, status=200, encoding=""):
    handlers = []

    class Response(io.BytesIO):
        pass

    response = Response(body)
    response.status = status
    response.headers = {"Content-Encoding": encoding}

    class Opener:
        def open(self, request, *, timeout):
            assert request.get_method() == "GET"
            assert request.get_header("Cache-control") == "no-cache"
            return response

    def opener(*args):
        handlers.extend(args)
        return Opener()

    monkeypatch.setattr(fetch, "_validate_resolved_destination", lambda *a, **kw: None)
    monkeypatch.setattr(fetch.urllib.request, "build_opener", opener)
    return handlers


@pytest.mark.parametrize("compress", [False, True])
def test_fetch_preserves_entity_bytes_and_uses_verified_tls(monkeypatch, compress):
    original = b"\xef\xbb\xbfmode: rule\r\n"
    handlers = _transport(
        monkeypatch,
        gzip.compress(original) if compress else original,
        encoding="gzip" if compress else "",
    )
    assert (
        fetch.fetch_https_bytes("https://entry.test/token", timeout=10, max_bytes=100) == original
    )
    tls = next(handler for handler in handlers if isinstance(handler, fetch._PinnedHTTPSHandler))
    assert tls._context.check_hostname
    assert tls._context.verify_mode == ssl.CERT_REQUIRED
    proxy = next(
        handler for handler in handlers if isinstance(handler, urllib.request.ProxyHandler)
    )
    assert proxy.proxies == {}
    redirect = next(
        handler for handler in handlers if isinstance(handler, urllib.request.HTTPRedirectHandler)
    )
    with pytest.raises(FetchError, match="redirected"):
        redirect.redirect_request(None, None, 302, "", {}, "https://other.test/token")


@pytest.mark.parametrize("status", [204, 206, 304, 401, 403, 500])
def test_only_complete_http_success_is_accepted(monkeypatch, status):
    _transport(monkeypatch, b"body", status=status)
    with pytest.raises(FetchError, match="HTTP 200"):
        fetch.fetch_https_bytes("https://entry.test/token", timeout=10, max_bytes=100)


def test_expanded_response_size_is_bounded(monkeypatch):
    _transport(monkeypatch, gzip.compress(b"x" * 10_000), encoding="gzip")
    with pytest.raises(FetchError, match="byte limit"):
        fetch.fetch_https_bytes("https://entry.test/token", timeout=10, max_bytes=200)
