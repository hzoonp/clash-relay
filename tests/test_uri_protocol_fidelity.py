from __future__ import annotations

import base64

import pytest
import yaml

from clash_relay.errors import SubscriptionError
from clash_relay.subscription_parser import parse_subscription


def _ss_userinfo(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def test_hysteria2_preserves_complete_auth_and_default_port() -> None:
    node = parse_subscription(
        "hy2://alice:sec%3Aret@hy.invalid.example?obfs=salamander"
        "&obfs-password=hide%25me&sni=cert.invalid.example#HY2"
    ).proxies[0]
    assert node["password"] == "alice:sec:ret"
    assert node["port"] == 443
    assert node["obfs-password"] == "hide%me"
    assert node["sni"] == "cert.invalid.example"
    assert yaml.safe_load(yaml.safe_dump(node))["password"] == "alice:sec:ret"


@pytest.mark.parametrize(
    "query",
    [
        "?pinSHA256=fingerprint",
        "?ech=blob",
        "?insecure=1",
        "?obfs=salamander",
        "?obfs=unknown&obfs-password=x",
    ],
)
def test_hysteria2_rejects_unrepresented_tls_or_transport(query: str) -> None:
    with pytest.raises(SubscriptionError, match="unsupported"):
        parse_subscription(f"hy2://pass@hy.invalid.example:443{query}")


def test_hysteria2_rejects_port_hopping_until_proxy_model_supports_it() -> None:
    with pytest.raises(SubscriptionError, match="port"):
        parse_subscription("hy2://pass@hy.invalid.example:443,444")


@pytest.mark.parametrize("password", ["literal%41pass", "x%25y", "a:b@c/d+é"])
def test_sip002_base64_userinfo_preserves_literal_password(password: str) -> None:
    node = parse_subscription(
        f"ss://{_ss_userinfo('aes-256-gcm:' + password)}@ss.invalid.example:8388"
    ).proxies[0]
    assert node["password"] == password


def test_sip002_plain_userinfo_is_percent_decoded_once() -> None:
    node = parse_subscription("ss://aes-256-gcm:literal%2541pass@ss.invalid.example:8388").proxies[
        0
    ]
    assert node["password"] == "literal%41pass"


def test_sip002_obfs_plugin_maps_to_mihomo_options() -> None:
    node = parse_subscription(
        "ss://aes-256-gcm:pass@ss.invalid.example:8388"
        "?plugin=obfs-local%3Bobfs%3Dhttp%3Bobfs-host%3Dcdn.invalid.example"
    ).proxies[0]
    assert node["plugin"] == "obfs"
    assert node["plugin-opts"] == {"mode": "http", "host": "cdn.invalid.example"}


def test_sip002_v2ray_plugin_maps_to_mihomo_options() -> None:
    node = parse_subscription(
        "ss://aes-256-gcm:pass@ss.invalid.example:8388"
        "?plugin=v2ray-plugin%3Bmode%3Dwebsocket%3Btls%3Bhost%3Dcdn.invalid.example%3Bpath%3D%2Fws"
    ).proxies[0]
    assert node["plugin"] == "v2ray-plugin"
    assert node["plugin-opts"] == {
        "mode": "websocket",
        "tls": True,
        "host": "cdn.invalid.example",
        "path": "/ws",
    }


def test_sip002_unknown_plugin_option_is_rejected() -> None:
    with pytest.raises(SubscriptionError, match="unsupported"):
        parse_subscription(
            "ss://aes-256-gcm:pass@ss.invalid.example:8388"
            "?plugin=obfs-local%3Bobfs%3Dhttp%3Bobfs-host%3Dcdn.invalid.example%3Bunknown%3Dx"
        )
