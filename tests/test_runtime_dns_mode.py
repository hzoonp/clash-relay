from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from clash_relay.runtime_config_renderer import RuntimeConfigRenderer


def _runtime(dns: dict[str, object]) -> dict[str, object]:
    return {
        "runtime": {
            "mixed_port": 7890,
            "allow_lan": False,
            "bind_address": "127.0.0.1",
            "mode": "rule",
            "log_level": "warning",
            "ipv6": False,
            "unified_delay": True,
            "tcp_concurrent": True,
            "profile": {"store_selected": True, "store_fake_ip": True},
            "dns": dns,
        }
    }


def _managed_dns() -> dict[str, object]:
    return {
        "mode": "managed",
        "enabled": True,
        "ipv6": False,
        "enhanced_mode": "fake-ip",
        "listen": "127.0.0.1:1053",
        "respect_rules": True,
        "default_nameservers": ["1.1.1.1", "8.8.8.8"],
        "nameservers": ["https://1.1.1.1/dns-query"],
        "proxy_server_nameservers": ["https://1.1.1.1/dns-query"],
        "fallback_nameservers": ["tls://1.0.0.1:853"],
        "fake_ip_range": "198.18.0.1/16",
        "fake_ip_filter_mode": "blacklist",
        "fake_ip_filter": ["*.lan", "*.local", "localhost"],
    }


def test_client_dns_mode_leaves_dns_to_the_client() -> None:
    output = RuntimeConfigRenderer().render(_runtime({"mode": "client"}))

    assert "dns" not in output
    assert output["profile"] == {"store-selected": True}


def test_managed_dns_mode_preserves_explicit_dns_runtime() -> None:
    output = RuntimeConfigRenderer().render(_runtime(_managed_dns()))

    assert output["profile"] == {"store-selected": True, "store-fake-ip": True}
    assert output["dns"] == {
        "enable": True,
        "enhanced-mode": "fake-ip",
        "listen": "127.0.0.1:1053",
        "nameserver": ["https://1.1.1.1/dns-query"],
        "fallback": ["tls://1.0.0.1:853"],
        "ipv6": False,
        "respect-rules": True,
        "default-nameserver": ["1.1.1.1", "8.8.8.8"],
        "proxy-server-nameserver": ["https://1.1.1.1/dns-query"],
        "fake-ip-range": "198.18.0.1/16",
        "fake-ip-filter-mode": "blacklist",
        "fake-ip-filter": ["*.lan", "*.local", "localhost"],
    }


def test_legacy_dns_without_mode_defaults_to_managed() -> None:
    dns = _managed_dns()
    dns.pop("mode")
    output = RuntimeConfigRenderer().render(_runtime(dns))

    assert "dns" in output
    assert output["profile"]["store-fake-ip"] is True


def test_dns_schema_accepts_client_and_legacy_managed_shapes() -> None:
    schema = json.loads(Path("schemas/config.schema.json").read_text(encoding="utf-8"))
    dns_schema = schema["properties"]["runtime"]["properties"]["dns"]
    validator = Draft202012Validator(dns_schema)

    validator.validate({"mode": "client"})
    legacy = _managed_dns()
    legacy.pop("mode")
    validator.validate(legacy)


def test_dns_schema_requires_proxy_server_nameserver_when_respecting_rules() -> None:
    schema = json.loads(Path("schemas/config.schema.json").read_text(encoding="utf-8"))
    dns_schema = schema["properties"]["runtime"]["properties"]["dns"]
    validator = Draft202012Validator(dns_schema)
    invalid = _managed_dns()
    invalid.pop("proxy_server_nameservers")

    errors = list(validator.iter_errors(invalid))

    assert errors
    assert any("proxy_server_nameservers" in error.message for error in errors)
