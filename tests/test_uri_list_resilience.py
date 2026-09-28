from __future__ import annotations

import base64

import pytest

from clash_relay.errors import SubscriptionError
from clash_relay.subscription_parser import parse_subscription

_VALID = "trojan://secret@node.invalid.example:443#Good"


@pytest.mark.parametrize(
    ("extra", "reason"),
    [
        ("garbage text", "garbage_uri_line"),
        ("unknown://node.invalid.example:443", "unknown_uri_scheme"),
        ("trojan://node.invalid.example:443", "invalid_auth"),
        ("hy2://pass@hy.invalid.example:443?ech=blob", "unsupported_uri_parameter"),
    ],
)
@pytest.mark.parametrize("envelope", [False, True])
def test_mixed_uri_list_skips_bad_item(extra: str, reason: str, envelope: bool) -> None:
    payload = f"# comment\n{_VALID}\n\n{extra}\n"
    if envelope:
        payload = base64.b64encode(payload.encode()).decode()
    parsed = parse_subscription(payload, invalid_policy="skip")
    assert [item["name"] for item in parsed.proxies] == ["Good"]
    assert parsed.skipped_items == 1
    assert parsed.skipped_reason_counts == ((reason, 1),)


def test_mixed_uri_list_error_policy_fails() -> None:
    with pytest.raises(SubscriptionError, match="garbage URI line"):
        parse_subscription(f"{_VALID}\ngarbage text\n", invalid_policy="error")


def test_unknown_scheme_only_is_a_skippable_uri_list() -> None:
    parsed = parse_subscription("unknown://node.invalid.example:443", invalid_policy="skip")
    assert parsed.proxies == ()
    assert parsed.skipped_reason_counts == (("unknown_uri_scheme", 1),)


def test_html_error_page_is_not_a_uri_list() -> None:
    with pytest.raises(SubscriptionError, match="neither Clash YAML"):
        parse_subscription(
            '<html>\n<a href="https://login.invalid.example">login</a>\n</html>',
            invalid_policy="skip",
        )
