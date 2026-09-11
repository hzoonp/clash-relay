from __future__ import annotations

import socket
import ssl
import urllib.error

import pytest

import clash_relay.ai_qualification as ai
import clash_relay.browsing_qualification as browsing
import clash_relay.transport_qualification as transport
from clash_relay.errors import ValidationError


class _ExitedProcess:
    def poll(self) -> int:
        return 1


class _JsonListResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return b"[]"


@pytest.mark.parametrize(
    "value",
    ["", "099", "600", "500-400", "abc", "200-"],
)
def test_ai_expected_status_parser_fails_closed(value: str) -> None:
    with pytest.raises(ValidationError, match=r"expected status|invalid AI qualification"):
        ai._expected_status_ranges(value)


def test_ai_expected_status_parser_accepts_bounded_compound_ranges() -> None:
    assert ai._expected_status_ranges("200-299/304") == ((200, 299), (304, 304))
    assert ai._status_matches("200-299/304", 204) is True
    assert ai._status_matches("200-299/304", 500) is False


def test_ai_provider_inventory_rejects_malformed_candidates() -> None:
    with pytest.raises(ValidationError, match="must be a mapping"):
        ai._ai_provider_payloads({"proxy-providers": []})
    with pytest.raises(ValidationError, match="provider payload is invalid"):
        ai._ai_provider_payloads({"proxy-providers": {"cr_ai_bad": {"payload": {}}}})
    with pytest.raises(ValidationError, match="unnamed proxy"):
        ai._ai_provider_payloads({"proxy-providers": {"cr_ai_bad": {"payload": [{"type": "ss"}]}}})
    with pytest.raises(ValidationError, match="no candidate AI proxy nodes"):
        ai._ai_provider_payloads({"proxy-providers": {"cr_general_any": {"payload": []}}})


def test_ai_temporary_probe_rejects_disappeared_provider() -> None:
    with pytest.raises(ValidationError, match="disappeared"):
        ai._temporary_probe_config(
            {"proxy-providers": {}},
            provider_name="cr_ai_us_us",
            payload=({"name": "node"},),
            mixed_port=7890,
            controller_port=9090,
            secret="secret",
        )


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (urllib.error.URLError(TimeoutError()), "timeout"),
        (urllib.error.URLError(socket.gaierror()), "dns_error"),
        (urllib.error.URLError(ssl.SSLError("tls")), "tls_error"),
        (ConnectionResetError(), "connection_error"),
        (OSError("generic"), "network_error"),
    ],
)
def test_ai_network_outcome_is_privacy_safe_and_structured(
    exc: BaseException,
    expected: str,
) -> None:
    assert ai._network_outcome(exc) == expected


def test_ai_controller_waits_fail_closed_when_mihomo_exits() -> None:
    process = _ExitedProcess()
    with pytest.raises(ValidationError, match="before AI qualification"):
        ai._wait_for_controller(process, 9090, "secret")
    with pytest.raises(ValidationError, match="loading AI qualification provider"):
        ai._wait_for_selector_members(process, 9090, "secret", {"node"})


def test_browsing_inventory_and_empty_filter_fail_closed() -> None:
    with pytest.raises(ValidationError, match="automatic browsing filter cannot be empty"):
        browsing._exact_filter(set())
    with pytest.raises(ValidationError, match="must be a mapping"):
        browsing._browsing_provider_payloads({"proxy-providers": []})
    with pytest.raises(ValidationError, match="provider must not be empty"):
        browsing._browsing_provider_payloads(
            {"proxy-providers": {"cr_browsing_us": {"payload": []}}}
        )
    with pytest.raises(ValidationError, match="unnamed proxy"):
        browsing._browsing_provider_payloads(
            {"proxy-providers": {"cr_browsing_us": {"payload": [{"type": "ss"}]}}}
        )
    with pytest.raises(ValidationError, match="no candidate browsing proxy nodes"):
        browsing._browsing_provider_payloads({"proxy-providers": {}})


def test_browsing_temporary_probe_rejects_invalid_or_disappeared_provider() -> None:
    with pytest.raises(ValidationError, match="must be a mapping"):
        browsing._temporary_probe_config(
            {"proxy-providers": []},
            {},
            mixed_port=7890,
            controller_port=9090,
            secret="secret",
        )
    with pytest.raises(ValidationError, match="disappeared"):
        browsing._temporary_probe_config(
            {"proxy-providers": {}},
            {"cr_browsing_us": ({"name": "node"},)},
            mixed_port=7890,
            controller_port=9090,
            secret="secret",
        )


def test_browsing_controller_waits_fail_closed_when_mihomo_exits() -> None:
    process = _ExitedProcess()
    with pytest.raises(ValidationError, match="before browsing qualification"):
        browsing._wait_for_controller(process, 9090, "secret")
    with pytest.raises(ValidationError, match="loading browsing qualification providers"):
        browsing._wait_for_members(process, 9090, "secret", {"node"})


def test_transport_empty_filter_and_temporary_probe_fail_closed() -> None:
    with pytest.raises(ValidationError, match="empty automatic filter"):
        transport._exact_filter(set())
    with pytest.raises(ValidationError, match="must be a mapping"):
        transport._temporary_probe_config(
            {"proxy-providers": []},
            {},
            mixed_port=7890,
            controller_port=9090,
            secret="secret",
        )
    with pytest.raises(ValidationError, match="disappeared"):
        transport._temporary_probe_config(
            {"proxy-providers": {}},
            {"cr_general_us": ({"name": "node"},)},
            mixed_port=7890,
            controller_port=9090,
            secret="secret",
        )


def test_transport_controller_rejects_non_object_response(monkeypatch) -> None:
    monkeypatch.setattr(
        transport.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _JsonListResponse(),
    )
    with pytest.raises(ValidationError, match="invalid response"):
        transport._controller_get(9090, "secret", "/version")


def test_transport_controller_waits_fail_closed_when_mihomo_exits() -> None:
    process = _ExitedProcess()
    with pytest.raises(ValidationError, match="before transport qualification"):
        transport._wait_for_controller(process, 9090, "secret")
    with pytest.raises(ValidationError, match="loading transport providers"):
        transport._wait_for_members(process, 9090, "secret", {"node"})
