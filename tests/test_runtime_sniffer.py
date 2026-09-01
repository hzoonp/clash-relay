from __future__ import annotations

from pathlib import Path

import pytest

from clash_relay.config_loader import load_project
from clash_relay.errors import ConfigurationError
from clash_relay.generator import _runtime_config


def _sniffer() -> dict[str, object]:
    return {
        "enabled": True,
        "force_dns_mapping": False,
        "parse_pure_ip": True,
        "sniff": {
            "http": {"ports": [80, "8080-8880"], "override_destination": True},
            "tls": {"ports": [443, 8443]},
            "quic": {"ports": [443, 8443]},
        },
    }


def _runtime(*, dns: dict[str, object], sniffer: dict[str, object] | None) -> dict[str, object]:
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
    if sniffer is not None:
        runtime["sniffer"] = sniffer
    return {"runtime": runtime}


def _managed_dns() -> dict[str, object]:
    return {
        "mode": "managed",
        "enabled": True,
        "enhanced_mode": "fake-ip",
        "listen": "127.0.0.1:1053",
        "nameservers": ["https://1.1.1.1/dns-query"],
        "fallback_nameservers": [],
    }


def _expected_sniffer() -> dict[str, object]:
    return {
        "enable": True,
        "force-dns-mapping": False,
        "parse-pure-ip": True,
        "sniff": {
            "HTTP": {"ports": [80, "8080-8880"], "override-destination": True},
            "TLS": {"ports": [443, 8443]},
            "QUIC": {"ports": [443, 8443]},
        },
    }


def test_client_owned_dns_can_enable_sniffer_without_fake_ip() -> None:
    output = _runtime_config(_runtime(dns={"mode": "client"}, sniffer=_sniffer()))

    assert output["sniffer"] == _expected_sniffer()
    assert "dns" not in output
    assert output["profile"] == {"store-selected": True}


def test_managed_dns_and_sniffer_are_independent() -> None:
    output = _runtime_config(_runtime(dns=_managed_dns(), sniffer=_sniffer()))

    assert output["sniffer"] == _expected_sniffer()
    assert "dns" in output
    assert output["profile"]["store-fake-ip"] is True


def test_legacy_runtime_without_sniffer_preserves_previous_output() -> None:
    output = _runtime_config(_runtime(dns={"mode": "client"}, sniffer=None))

    assert "sniffer" not in output
    assert "dns" not in output


@pytest.mark.parametrize("port_range", ["8443-443", "1-70000", "65536-65536"])
def test_invalid_sniffer_port_ranges_fail_project_loading(
    project_factory, yaml_editor, port_range: str
) -> None:
    _, paths = project_factory()

    def mutate(data: dict) -> None:
        data["runtime"]["sniffer"] = _sniffer()
        data["runtime"]["sniffer"]["sniff"]["quic"]["ports"] = [port_range]

    yaml_editor(paths["config_path"], mutate)
    with pytest.raises(ConfigurationError, match="invalid range"):
        load_project(**paths)


def test_public_example_declares_client_dns_and_sniffer(repo_root: Path) -> None:
    import yaml

    data = yaml.safe_load((repo_root / "config.example.yaml").read_text(encoding="utf-8"))
    assert data["runtime"]["dns"] == {"mode": "client"}
    assert data["runtime"]["sniffer"] == _sniffer()
