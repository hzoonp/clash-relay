from __future__ import annotations

from clash_relay.builder import _empty_subscription_failure_reason
from clash_relay.subscription_parser import ParsedSubscription


def test_empty_subscription_failure_reason_distinguishes_cases() -> None:
    empty = ParsedSubscription(proxies=(), skipped_items=0)
    unsupported = ParsedSubscription(
        proxies=(),
        skipped_items=2,
        skipped_reason_counts=(("unsupported_type", 2),),
    )
    mixed = ParsedSubscription(
        proxies=(),
        skipped_items=2,
        skipped_reason_counts=(
            ("invalid_port", 1),
            ("unsupported_type", 1),
        ),
    )

    assert _empty_subscription_failure_reason(empty) == "empty_subscription"
    assert _empty_subscription_failure_reason(unsupported) == "all_unsupported_types"
    assert _empty_subscription_failure_reason(mixed) == "mixed_invalid_proxies"
