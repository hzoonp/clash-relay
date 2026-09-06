"""Bounded retry wrapper for immutable, public pinned upstream text assets."""

from __future__ import annotations

import time
import urllib.error
from urllib.parse import urlsplit

from .errors import FetchError
from .fetch import fetch_subscription

_RETRYABLE_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3


def _retryable(exc: FetchError) -> bool:
    cause = exc.__cause__
    if isinstance(cause, urllib.error.HTTPError):
        return cause.code in _RETRYABLE_HTTP
    return isinstance(cause, (urllib.error.URLError, TimeoutError, ConnectionError, OSError))


def fetch_pinned_text(
    url: str,
    *,
    timeout: int,
    max_bytes: int,
    allow_http: bool,
    allow_file: bool,
) -> str:
    """Fetch a pinned raw GitHub asset with three transient-only attempts."""

    parsed = urlsplit(url)
    pinned_raw_github = (
        parsed.scheme == "https"
        and parsed.hostname == "raw.githubusercontent.com"
        and not allow_http
        and not allow_file
    )
    if not pinned_raw_github:
        return fetch_subscription(
            url,
            timeout=timeout,
            max_bytes=max_bytes,
            allow_http=allow_http,
            allow_file=allow_file,
        )

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return fetch_subscription(
                url,
                timeout=timeout,
                max_bytes=max_bytes,
                allow_http=False,
                allow_file=False,
            )
        except FetchError as exc:
            if not _retryable(exc) or attempt == _MAX_ATTEMPTS:
                raise
            time.sleep(float(attempt))
    raise AssertionError("unreachable")
