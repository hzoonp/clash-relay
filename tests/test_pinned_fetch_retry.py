from __future__ import annotations

import urllib.error

import pytest

import clash_relay.pinned_fetch as pinned_fetch
from clash_relay.errors import FetchError


def _fetch_error(cause: BaseException) -> FetchError:
    try:
        raise cause
    except BaseException as caught:
        try:
            raise FetchError("safe fetch failure") from caught
        except FetchError as wrapped:
            return wrapped


def _call() -> str:
    return pinned_fetch.fetch_pinned_text(
        "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/abc/Clash/Microsoft.list",
        timeout=20,
        max_bytes=1024,
        allow_http=False,
        allow_file=False,
    )


def test_pinned_fetch_recovers_from_connection_reset(monkeypatch) -> None:
    calls = 0
    sleeps: list[float] = []

    def fake_fetch(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _fetch_error(ConnectionResetError("reset"))
        return "OK"

    monkeypatch.setattr(pinned_fetch, "fetch_subscription", fake_fetch)
    monkeypatch.setattr(pinned_fetch.time, "sleep", sleeps.append)

    assert _call() == "OK"
    assert calls == 3
    assert sleeps == [1.0, 2.0]


def test_pinned_fetch_exhausts_after_three_transient_attempts(monkeypatch) -> None:
    calls = 0

    def fake_fetch(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise _fetch_error(urllib.error.URLError("temporary"))

    monkeypatch.setattr(pinned_fetch, "fetch_subscription", fake_fetch)
    monkeypatch.setattr(pinned_fetch.time, "sleep", lambda _seconds: None)

    with pytest.raises(FetchError, match="safe fetch failure"):
        _call()
    assert calls == 3


def test_pinned_fetch_retries_503_but_not_404(monkeypatch) -> None:
    calls = 0

    def fake_503(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _fetch_error(
                urllib.error.HTTPError("https://example.invalid", 503, "busy", None, None)
            )
        return "OK"

    monkeypatch.setattr(pinned_fetch, "fetch_subscription", fake_503)
    monkeypatch.setattr(pinned_fetch.time, "sleep", lambda _seconds: None)
    assert _call() == "OK"
    assert calls == 2

    calls = 0

    def fake_404(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise _fetch_error(
            urllib.error.HTTPError("https://example.invalid", 404, "missing", None, None)
        )

    monkeypatch.setattr(pinned_fetch, "fetch_subscription", fake_404)
    with pytest.raises(FetchError):
        _call()
    assert calls == 1


def test_non_network_fetch_error_is_not_retried(monkeypatch) -> None:
    calls = 0

    def fake_fetch(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise FetchError("semantic validation failure")

    monkeypatch.setattr(pinned_fetch, "fetch_subscription", fake_fetch)

    with pytest.raises(FetchError, match="semantic validation failure"):
        _call()
    assert calls == 1


def test_non_pinned_url_keeps_single_fetch_semantics(monkeypatch) -> None:
    calls = 0

    def fake_fetch(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise _fetch_error(ConnectionResetError("reset"))

    monkeypatch.setattr(pinned_fetch, "fetch_subscription", fake_fetch)

    with pytest.raises(FetchError):
        pinned_fetch.fetch_pinned_text(
            "https://subscriptions.example/private",
            timeout=20,
            max_bytes=1024,
            allow_http=False,
            allow_file=False,
        )
    assert calls == 1
