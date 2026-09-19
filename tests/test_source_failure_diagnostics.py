from __future__ import annotations

import socket
import ssl
import urllib.error

import pytest

from clash_relay.builder import _source_failure_diagnostic
from clash_relay.errors import FetchError, SubscriptionError, UnsafeSubscriptionError


@pytest.mark.parametrize(
    ("cause", "reason"),
    [
        (
            urllib.error.HTTPError(
                "https://example.invalid",
                403,
                "Forbidden",
                {},
                None,
            ),
            "http_error",
        ),
        (ssl.SSLError("private tls detail"), "tls_error"),
        (TimeoutError("private timeout detail"), "timeout"),
        (socket.gaierror(-2, "private dns detail"), "dns_error"),
    ],
)
def test_fetch_failure_causes_map_to_static_reasons(
    cause: BaseException,
    reason: str,
) -> None:
    error = FetchError("private source detail")
    error.__cause__ = cause

    assert _source_failure_diagnostic(error) == {
        "failure_category": "subscription_fetch",
        "failure_reason": reason,
    }


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        ("subscription URL is malformed", "invalid_url"),
        (
            "subscription URL may not target a private or special-use IP literal",
            "destination_rejected",
        ),
        ("subscription hostname resolved to no usable address", "dns_error"),
        ("subscription exceeds the configured byte limit", "size_limit"),
        ("subscription gzip payload is invalid", "payload_encoding"),
        ("cannot read local subscription fixture", "io_error"),
        ("private transport detail", "transport_error"),
    ],
)
def test_fetch_failure_messages_are_coarsened_to_static_reasons(
    message: str,
    reason: str,
) -> None:
    result = _source_failure_diagnostic(FetchError(message))

    assert result == {
        "failure_category": "subscription_fetch",
        "failure_reason": reason,
    }
    assert "private transport detail" not in repr(result)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            UnsafeSubscriptionError("private unsafe detail"),
            {
                "failure_category": "subscription_admission",
                "failure_reason": "unsafe_payload",
            },
        ),
        (
            SubscriptionError("subscription contains no usable proxies"),
            {
                "failure_category": "subscription_parse",
                "failure_reason": "no_usable_proxies",
            },
        ),
        (
            SubscriptionError("private parser detail"),
            {
                "failure_category": "subscription_parse",
                "failure_reason": "parse_error",
            },
        ),
        (
            OSError("private io detail"),
            {
                "failure_category": "io_failure",
                "failure_reason": "io_error",
            },
        ),
        (
            ValueError("private value detail"),
            {
                "failure_category": "subscription_parse",
                "failure_reason": "invalid_value",
            },
        ),
    ],
)
def test_source_failures_never_reflect_exception_text(
    error: BaseException,
    expected: dict[str, str],
) -> None:
    result = _source_failure_diagnostic(error)

    assert result == expected
    assert str(error) not in repr(result)
