from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from clash_relay.generator import _runtime_config


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
        "enhanced_mode": "fake-ip",
        "listen": "127.0.0.1:1053",
        "nameservers": ["https://1.1.1.1/dns-query"],
        "fallback_nameservers": ["tls://1.0.0.1:853"],
    }


def test_client_dns_mode_leaves_dns_to_the_client() -> None:
    output = _runtime_config(_runtime({"mode": "client"}))

    assert "dns" not in output
    assert output["profile"] == {"store-selected": True}


def test_managed_dns_mode_preserves_explicit_dns_runtime() -> None:
    output = _runtime_config(_runtime(_managed_dns()))

    assert output["profile"] == {"store-selected": True, "store-fake-ip": True}
    assert output["dns"] == {
        "enable": True,
        "enhanced-mode": "fake-ip",
        "listen": "127.0.0.1:1053",
        "nameserver": ["https://1.1.1.1/dns-query"],
        "fallback": ["tls://1.0.0.1:853"],
    }


def test_legacy_dns_without_mode_defaults_to_managed() -> None:
    dns = _managed_dns()
    dns.pop("mode")
    output = _runtime_config(_runtime(dns))

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
