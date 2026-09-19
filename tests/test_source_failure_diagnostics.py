from __future__ import annotations

import socket
import ssl
import urllib.error

import pytest
import yaml

from clash_relay.builder import _source_failure_diagnostic, build_candidate
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


def test_all_invalid_optional_source_reports_static_reason(
    project_factory,
    fixture_env,
    yaml_editor,
) -> None:
    root, paths = project_factory()
    invalid_source = root / "invalid-secondary.yaml"
    invalid_source.write_text(
        yaml.safe_dump(
            {
                "proxies": [
                    {
                        "name": "Private Unsupported Node",
                        "type": "unsupported-private-type",
                        "server": "secret.invalid.example",
                        "port": 443,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def skip_invalid(document):
        document["generation"]["invalid_proxy_policy"] = "skip"

    yaml_editor(paths["config_path"], skip_invalid)
    env = dict(fixture_env)
    env["SUB_SECONDARY"] = invalid_source.resolve().as_uri()

    result = build_candidate(**paths, env=env)
    report = next(
        item for item in result.report["subscriptions"] if item["id"] == "secondary"
    )

    assert report["status"] == "failed"
    assert report["failure_category"] == "subscription_parse"
    assert report["failure_reason"] == "all_unsupported_types"
    assert report["skipped_invalid_nodes"] == 1
    assert "Private Unsupported Node" not in repr(
        {
            "failure_category": report["failure_category"],
            "failure_reason": report["failure_reason"],
        }
    )
