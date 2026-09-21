from __future__ import annotations

import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from clash_relay.runtime_config_renderer import RuntimeConfigRenderer


def _runtime(dns: dict[str, object], tun: dict[str, object] | None = None) -> dict[str, object]:
    runtime: dict[str, object] = {
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
    if tun is not None:
        runtime["tun"] = tun
    return {"runtime": runtime}


def _managed_dns() -> dict[str, object]:
    return {
        "mode": "managed",
        "enabled": True,
        "ipv6": False,
        "enhanced_mode": "fake-ip",
        "listen": "127.0.0.1:1053",
        "respect_rules": True,
        "default_nameservers": [
            "https://1.1.1.1/dns-query",
            "tls://1.0.0.1:853",
        ],
        "nameservers": ["https://1.1.1.1/dns-query"],
        "proxy_server_nameservers": ["https://1.1.1.1/dns-query"],
        "direct_nameservers": ["https://dns.alidns.com/dns-query"],
        "direct_nameserver_follow_policy": True,
        "routing_policy": "acl4ssr",
        "fallback_nameservers": [],
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
        "fallback": [],
        "ipv6": False,
        "respect-rules": True,
        "default-nameserver": [
            "https://1.1.1.1/dns-query",
            "tls://1.0.0.1:853",
        ],
        "proxy-server-nameserver": ["https://1.1.1.1/dns-query"],
        "direct-nameserver": ["https://dns.alidns.com/dns-query"],
        "direct-nameserver-follow-policy": True,
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


def _managed_tun() -> dict[str, object]:
    return {
        "mode": "managed",
        "enabled": True,
        "stack": "mixed",
        "dns_hijack": ["any:53", "tcp://any:53"],
        "auto_route": True,
        "auto_detect_interface": True,
        "strict_route": True,
    }


def test_managed_tun_renders_dns_hijack_and_strict_route() -> None:
    output = RuntimeConfigRenderer().render(_runtime(_managed_dns(), _managed_tun()))

    assert output["tun"] == {
        "enable": True,
        "stack": "mixed",
        "dns-hijack": ["any:53", "tcp://any:53"],
        "auto-route": True,
        "auto-detect-interface": True,
        "strict-route": True,
    }


def test_client_tun_mode_is_omitted() -> None:
    output = RuntimeConfigRenderer().render(_runtime(_managed_dns(), {"mode": "client"}))

    assert "tun" not in output


def test_canonical_flclash_profile_keeps_tun_client_owned(repo_root: Path) -> None:
    config = yaml.safe_load((repo_root / "config.yaml").read_text(encoding="utf-8"))

    assert config["runtime"]["tun"] == {"mode": "client"}
    rendered = RuntimeConfigRenderer().render(config)
    assert "tun" not in rendered
    assert rendered["dns"]["enable"] is True
    assert rendered["dns"]["enhanced-mode"] == "fake-ip"
