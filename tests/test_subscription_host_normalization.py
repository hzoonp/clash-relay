from __future__ import annotations

import pytest
import yaml

from clash_relay.errors import SubscriptionError
from clash_relay.subscription_parser import parse_subscription


def _http(server: str) -> dict[str, object]:
    return {"name": "Node", "type": "http", "server": server, "port": 443}


@pytest.mark.parametrize(
    "server",
    [
        " 127.0.0.1 ",
        " [::1] ",
        " localhost ",
        " localhost. ",
        " localhost.localdomain ",
    ],
)
def test_private_host_rejection_uses_normalized_server(server: str) -> None:
    with pytest.raises(SubscriptionError, match="private or special-use"):
        parse_subscription(yaml.safe_dump({"proxies": [_http(server)]}))


def test_public_hostname_is_normalized_before_storage() -> None:
    result = parse_subscription(yaml.safe_dump({"proxies": [_http(" node.invalid.example ")]}))

    assert result.proxies[0]["server"] == "node.invalid.example"
