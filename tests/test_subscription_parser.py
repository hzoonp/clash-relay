from __future__ import annotations

import base64
from pathlib import Path

import pytest
import yaml

from clash_relay.errors import SubscriptionError
from clash_relay.subscription_parser import parse_subscription


def _http(name: str = "Node", server: str = "node.invalid.example", port=443):
    return {"name": name, "type": "http", "server": server, "port": port}


def test_parse_clash_mapping() -> None:
    result = parse_subscription(yaml.safe_dump({"proxies": [_http()]}))
    assert len(result.proxies) == 1
    assert result.proxies[0]["type"] == "http"


def test_parse_plain_proxy_list() -> None:
    result = parse_subscription(yaml.safe_dump([_http("A"), _http("B", port=444)]))
    assert [item["name"] for item in result.proxies] == ["A", "B"]


def test_parse_inline_provider_payload() -> None:
    document = {
        "proxy-providers": {
            "inline": {"type": "inline", "payload": [_http("Inline")]},
            "remote-is-never-followed": {
                "type": "http",
                "url": "https://ignored.invalid/secret",
            },
        }
    }
    result = parse_subscription(yaml.safe_dump(document))
    assert [item["name"] for item in result.proxies] == ["Inline"]


def test_remote_provider_is_not_followed_and_yields_empty() -> None:
    document = {
        "proxy-providers": {"remote": {"type": "http", "url": "https://ignored.invalid/secret"}}
    }
    result = parse_subscription(yaml.safe_dump(document))
    assert result.proxies == ()


def test_parse_uri_fixture(repo_root: Path) -> None:
    text = (repo_root / "tests/fixtures/subscriptions/uris.txt").read_text(encoding="utf-8")
    result = parse_subscription(text)
    assert [item["type"] for item in result.proxies] == [
        "ss",
        "vmess",
        "vless",
        "trojan",
        "hysteria2",
    ]


def test_parse_base64_encoded_uri_lines(repo_root: Path) -> None:
    raw = (repo_root / "tests/fixtures/subscriptions/uris.txt").read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    result = parse_subscription(encoded)
    assert len(result.proxies) == 5


@pytest.mark.parametrize(
    ("uri", "proxy_type"),
    [
        ("http://user:pass@http.invalid.example:80#HTTP", "http"),
        ("https://user:pass@https.invalid.example:443#HTTPS", "http"),
        ("socks5://user:pass@socks.invalid.example:1080#SOCKS", "socks5"),
        ("trojan://pass@trojan.invalid.example:443#Trojan", "trojan"),
        ("vless://00000000-0000-4000-8000-000000000009@v.invalid.example:443#VLESS", "vless"),
        ("hysteria2://pass@hy.invalid.example:443#HY2", "hysteria2"),
        ("hy2://pass@hy.invalid.example:443#HY2", "hysteria2"),
        (
            "tuic://00000000-0000-4000-8000-000000000010:pass@tuic.invalid.example:443#TUIC",
            "tuic",
        ),
        ("anytls://pass@any.invalid.example:443#AnyTLS", "anytls"),
    ],
)
def test_common_uri_schemes(uri: str, proxy_type: str) -> None:
    result = parse_subscription(uri)
    assert result.proxies[0]["type"] == proxy_type


def test_empty_subscription_is_parseable_but_has_no_nodes() -> None:
    assert parse_subscription("\n\n").proxies == ()


def test_invalid_payload_fails() -> None:
    with pytest.raises(SubscriptionError, match="neither Clash YAML"):
        parse_subscription("this is not a subscription!!!")


def test_invalid_proxy_error_policy() -> None:
    document = {"proxies": [_http(), {"name": "Broken", "type": "http"}]}
    with pytest.raises(SubscriptionError, match="invalid proxies"):
        parse_subscription(yaml.safe_dump(document), invalid_policy="error")


def test_invalid_proxy_skip_policy() -> None:
    document = {"proxies": [_http(), {"name": "Broken", "type": "http"}]}
    result = parse_subscription(yaml.safe_dump(document), invalid_policy="skip")
    assert len(result.proxies) == 1
    assert result.skipped_items == 1


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("grpc-opts", "not-a-mapping"),
        ("ws-opts", []),
        ("h2-opts", "invalid"),
        ("reality-opts", ["invalid"]),
        ("plugin-opts", "invalid"),
    ],
)
def test_malformed_structured_proxy_options_rejected(field: str, bad_value) -> None:
    proxy = {
        "name": "Malformed options",
        "type": "vless",
        "server": "vless.invalid.example",
        "port": 443,
        "uuid": "00000000-0000-4000-8000-000000000099",
        field: bad_value,
    }
    with pytest.raises(SubscriptionError, match="malformed structured option"):
        parse_subscription(yaml.safe_dump({"proxies": [proxy]}), invalid_policy="error")


def test_null_structured_proxy_options_are_removed_as_absent() -> None:
    proxy = {
        "name": "Null WebSocket options",
        "type": "vless",
        "server": "vless.invalid.example",
        "port": 443,
        "uuid": "00000000-0000-4000-8000-000000000099",
        "network": "ws",
        "ws-opts": None,
        "reality-opts": None,
    }

    result = parse_subscription(yaml.safe_dump({"proxies": [proxy]}), invalid_policy="error")

    assert len(result.proxies) == 1
    assert "ws-opts" not in result.proxies[0]
    assert "reality-opts" not in result.proxies[0]
    assert result.skipped_items == 0


def test_malformed_structured_proxy_options_are_skipped_before_inventory_admission() -> None:
    malformed = {
        "name": "Malformed gRPC",
        "type": "vless",
        "server": "broken.invalid.example",
        "port": 443,
        "uuid": "00000000-0000-4000-8000-000000000099",
        "network": "grpc",
        "grpc-opts": "invalid",
    }
    valid = {
        "name": "Valid gRPC",
        "type": "vless",
        "server": "valid.invalid.example",
        "port": 443,
        "uuid": "00000000-0000-4000-8000-000000000098",
        "network": "grpc",
        "grpc-opts": {"grpc-service-name": "service"},
    }
    result = parse_subscription(
        yaml.safe_dump({"proxies": [malformed, valid]}),
        invalid_policy="skip",
    )

    assert [proxy["name"] for proxy in result.proxies] == ["Valid gRPC"]
    assert result.skipped_items == 1


def test_yaml_aliases_rejected() -> None:
    payload = "proxies:\n  - &node {name: A, type: http, server: a.invalid.example, port: 80}\n  - *node\n"
    with pytest.raises(SubscriptionError, match="anchors and aliases"):
        parse_subscription(payload)


@pytest.mark.parametrize("server", ["127.0.0.1", "10.1.2.3", "::1", "169.254.1.2"])
def test_private_proxy_host_rejected_by_default(server: str) -> None:
    with pytest.raises(SubscriptionError, match="private or special-use"):
        parse_subscription(yaml.safe_dump({"proxies": [_http(server=server)]}))


def test_private_proxy_host_can_be_explicitly_allowed() -> None:
    result = parse_subscription(
        yaml.safe_dump({"proxies": [_http(server="127.0.0.1")]}),
        reject_private_hosts=False,
    )
    assert result.proxies[0]["server"] == "127.0.0.1"


@pytest.mark.parametrize(
    "field",
    ["dialer-proxy", "interface-name", "interface", "bind-interface", "routing-mark", "fwmark"],
)
def test_control_fields_are_stripped(field: str) -> None:
    proxy = _http()
    proxy[field] = "attacker-controlled"
    result = parse_subscription(yaml.safe_dump({"proxies": [proxy]}))
    assert field not in result.proxies[0]


def test_unsupported_proxy_type_rejected() -> None:
    proxy = _http()
    proxy["type"] = "mystery"
    with pytest.raises(SubscriptionError, match="unsupported type"):
        parse_subscription(yaml.safe_dump({"proxies": [proxy]}))


def test_required_protocol_field_rejected() -> None:
    proxy = {
        "name": "No password",
        "type": "trojan",
        "server": "trojan.invalid.example",
        "port": 443,
    }
    with pytest.raises(SubscriptionError, match="password"):
        parse_subscription(yaml.safe_dump({"proxies": [proxy]}))


def test_numeric_string_port_is_normalized() -> None:
    result = parse_subscription(yaml.safe_dump({"proxies": [_http(port="443")]}))
    assert result.proxies[0]["port"] == 443


@pytest.mark.parametrize("port", [0, 65536, -1, "not-a-number", True])
def test_invalid_ports_rejected(port) -> None:
    with pytest.raises(SubscriptionError, match="valid port"):
        parse_subscription(yaml.safe_dump({"proxies": [_http(port=port)]}))


def test_bom_is_accepted() -> None:
    result = parse_subscription("\ufeff" + yaml.safe_dump({"proxies": [_http()]}))
    assert len(result.proxies) == 1


def test_nul_byte_rejected() -> None:
    with pytest.raises(SubscriptionError, match="NUL"):
        parse_subscription("proxies:\x00 []")
