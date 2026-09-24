from __future__ import annotations

import pytest

from clash_relay.dns_runtime_audit import audit_dns_runtime_dependencies
from clash_relay.errors import ValidationError


def _candidate() -> dict:
    return {
        "dns": {
            "enable": True,
            "enhanced-mode": "fake-ip",
            "respect-rules": False,
            "default-nameserver": [
                "https://1.1.1.1/dns-query",
                "https://1.0.0.1/dns-query",
            ],
            "proxy-server-nameserver": [
                "https://1.1.1.1/dns-query",
                "https://8.8.8.8/dns-query",
            ],
            "direct-nameserver-follow-policy": False,
            "nameserver-policy": {"rule-set:acl4ssr_openai": ["https://dns.google/dns-query"]},
        },
        "proxy-groups": [
            {
                "name": "automatic",
                "type": "url-test",
                "url": "https://cp.cloudflare.com/generate_204",
                "timeout": 5000,
                "expected-status": 204,
            },
        ],
    }


def test_runtime_dns_audit_proves_resolver_transport_is_independent() -> None:
    assert audit_dns_runtime_dependencies(_candidate()) == {
        "status": "passed",
        "resolver_transport": "independent",
        "direct_resolver_policy": "bypass",
        "automatic_groups": 1,
    }


def test_runtime_dns_audit_accepts_domestic_plain_ip_bootstrap_and_eight_second_probe() -> None:
    candidate = _candidate()
    candidate["dns"]["default-nameserver"] = ["223.5.5.5", "119.29.29.29"]
    candidate["proxy-groups"][0]["timeout"] = 8000
    assert audit_dns_runtime_dependencies(candidate)["status"] == "passed"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("respect-rules", True, "must not respect"),
        ("direct-nameserver-follow-policy", True, "must bypass"),
        ("proxy-server-nameserver", ["https://dns.google/dns-query"], "IP-literal"),
    ],
)
def test_runtime_dns_audit_rejects_resolver_dependency_cycles(
    field: str, value: object, message: str
) -> None:
    candidate = _candidate()
    candidate["dns"][field] = value

    with pytest.raises(ValidationError, match=message):
        audit_dns_runtime_dependencies(candidate)


@pytest.mark.parametrize(
    "url",
    ["http://cp.cloudflare.com/generate_204", "https://www.gstatic.com/generate_204"],
)
def test_runtime_dns_audit_rejects_non_https_urltest(url: str) -> None:
    candidate = _candidate()
    candidate["proxy-groups"][0]["url"] = url

    with pytest.raises(ValidationError, match="canonical Cloudflare"):
        audit_dns_runtime_dependencies(candidate)


def test_runtime_dns_audit_rejects_short_urltest_timeout() -> None:
    candidate = _candidate()
    candidate["proxy-groups"][0]["timeout"] = 3000

    with pytest.raises(ValidationError, match="5000ms or 8000ms"):
        audit_dns_runtime_dependencies(candidate)


def test_runtime_dns_audit_rejects_any_other_automatic_timeout() -> None:
    candidate = _candidate()
    candidate["proxy-groups"][0]["timeout"] = 9000
    with pytest.raises(ValidationError, match="5000ms or 8000ms"):
        audit_dns_runtime_dependencies(candidate)


def test_runtime_dns_audit_leaves_ai_probe_semantics_untouched() -> None:
    candidate = _candidate()
    candidate["proxy-groups"].append(
        {
            "name": "__CR_AI_OPENAI_01",
            "type": "url-test",
            "url": "https://android.chat.openai.com/",
            "timeout": 7000,
            "expected-status": "200-399",
        }
    )
    assert audit_dns_runtime_dependencies(candidate)["status"] == "passed"
