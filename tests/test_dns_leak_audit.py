from __future__ import annotations

import pytest

from clash_relay.dns_leak_audit import audit_dns_leak_protection
from clash_relay.errors import ValidationError


def _candidate() -> dict:
    return {
        "tun": {
            "enable": True,
            "stack": "mixed",
            "dns-hijack": ["any:53", "tcp://any:53"],
            "auto-route": True,
            "auto-detect-interface": True,
            "strict-route": True,
        },
        "dns": {
            "enable": True,
            "ipv6": False,
            "enhanced-mode": "fake-ip",
            "listen": "127.0.0.1:1053",
            "respect-rules": True,
            "default-nameserver": [
                "https://1.1.1.1/dns-query",
                "tls://1.0.0.1:853",
            ],
            "nameserver": ["https://1.1.1.1/dns-query"],
            "proxy-server-nameserver": ["https://1.1.1.1/dns-query"],
            "direct-nameserver": ["https://dns.alidns.com/dns-query"],
            "direct-nameserver-follow-policy": True,
            "fallback": [],
            "nameserver-policy": {
                "rule-set:acl4ssr_china_domain": ["https://dns.alidns.com/dns-query"],
                "rule-set:acl4ssr_openai": ["https://1.1.1.1/dns-query"],
            },
        },
        "rule-providers": {
            "acl4ssr_china_domain": {"type": "inline"},
            "acl4ssr_openai": {"type": "inline"},
        },
    }


def test_dns_leak_audit_accepts_strict_tun_contract() -> None:
    report = audit_dns_leak_protection(_candidate())

    assert report["status"] == "passed"
    assert report["mode"] == "strict_tun"
    assert report["policy_rulesets"] == 2


def test_dns_leak_audit_rejects_system_resolver_escape() -> None:
    candidate = _candidate()
    candidate["dns"]["nameserver"] = ["system://"]

    with pytest.raises(ValidationError, match="encrypted nameserver"):
        audit_dns_leak_protection(candidate)


def test_dns_leak_audit_rejects_incomplete_port_53_hijack() -> None:
    candidate = _candidate()
    candidate["tun"]["dns-hijack"] = ["any:53"]

    with pytest.raises(ValidationError, match="UDP and TCP"):
        audit_dns_leak_protection(candidate)


def test_dns_leak_audit_rejects_standalone_dns_classification() -> None:
    candidate = _candidate()
    candidate["dns"]["nameserver-policy"]["+.example.com"] = ["https://1.1.1.1/dns-query"]

    with pytest.raises(ValidationError, match="standalone DNS classification"):
        audit_dns_leak_protection(candidate)


def test_dns_leak_audit_is_not_applicable_without_tun() -> None:
    assert audit_dns_leak_protection({"dns": {"enable": True}}) == {
        "status": "not_applicable",
        "mode": "no_tun",
    }


def test_dns_leak_audit_requires_ip_literal_bootstrap_resolvers() -> None:
    candidate = _candidate()
    candidate["dns"]["default-nameserver"] = ["https://dns.example/dns-query"]

    with pytest.raises(ValidationError, match="IP-literal"):
        audit_dns_leak_protection(candidate)
