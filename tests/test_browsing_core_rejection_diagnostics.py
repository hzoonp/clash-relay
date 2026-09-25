from __future__ import annotations

from clash_relay.browsing_qualification import _runtime_source_id


def test_runtime_source_id_recovers_canonical_subscription_id() -> None:
    proxy = {
        "name": "[BROWSING:US] sub_3/Example #abcdef1234",
        "type": "vless",
    }

    assert _runtime_source_id(proxy) == "sub_3"


def test_runtime_source_id_rejects_noncanonical_runtime_name() -> None:
    proxy = {"name": "private-node.example", "type": "vless"}

    assert _runtime_source_id(proxy) is None
