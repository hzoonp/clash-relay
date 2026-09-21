"""Static DNS leak-prevention audit for generated Mihomo candidates."""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

from .errors import ValidationError

_ENCRYPTED_DNS_SCHEMES = frozenset({"https", "tls", "quic", "h3"})


def _is_loopback_listener(value: Any) -> bool:
    if not isinstance(value, str) or ":" not in value:
        return False
    host = value.rsplit(":", 1)[0].strip("[]")
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _encrypted_resolver(value: Any, *, require_ip_host: bool = False) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = urlsplit(value.split("#", 1)[0])
    except ValueError:
        return False
    if parsed.scheme.lower() not in _ENCRYPTED_DNS_SCHEMES or not parsed.netloc:
        return False
    if not require_ip_host:
        return True
    hostname = parsed.hostname
    if not hostname:
        return False
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return True


def _resolver_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    return []


def audit_dns_leak_protection(config: dict[str, Any]) -> dict[str, Any]:
    """Fail closed when a generated managed-TUN profile could leak DNS."""

    tun = config.get("tun")
    if tun is None:
        return {"status": "not_applicable", "mode": "no_tun"}
    if not isinstance(tun, dict):
        raise ValidationError("DNS leak audit requires tun to be a mapping")
    if tun.get("enable") is not True:
        return {"status": "not_applicable", "mode": "tun_disabled"}

    dns = config.get("dns")
    if not isinstance(dns, dict) or dns.get("enable") is not True:
        raise ValidationError("DNS leak audit requires enabled managed DNS")
    if dns.get("enhanced-mode") != "fake-ip":
        raise ValidationError("DNS leak audit requires fake-ip mode")
    if dns.get("ipv6") is not False:
        raise ValidationError("DNS leak audit requires DNS IPv6 responses to be disabled")
    if dns.get("respect-rules") is not True:
        raise ValidationError("DNS leak audit requires respect-rules")
    if dns.get("direct-nameserver-follow-policy") is not True:
        raise ValidationError("DNS leak audit requires direct nameserver policy following")
    if dns.get("fallback") not in ([], None):
        raise ValidationError("DNS leak audit requires fallback DNS to remain disabled")
    if not _is_loopback_listener(dns.get("listen")):
        raise ValidationError("DNS leak audit requires a loopback-only DNS listener")

    for field in (
        "default-nameserver",
        "nameserver",
        "proxy-server-nameserver",
        "direct-nameserver",
    ):
        values = _resolver_values(dns.get(field))
        if not values:
            raise ValidationError(f"DNS leak audit requires non-empty {field}")
        require_ip_host = field == "default-nameserver"
        if not all(_encrypted_resolver(item, require_ip_host=require_ip_host) for item in values):
            suffix = " with IP-literal hosts" if require_ip_host else ""
            raise ValidationError(f"DNS leak audit requires encrypted {field} endpoints{suffix}")

    hijack = tun.get("dns-hijack")
    if not isinstance(hijack, list):
        raise ValidationError("DNS leak audit requires tun dns-hijack")
    hijack_values = {str(item) for item in hijack}
    if "any:53" not in hijack_values or "tcp://any:53" not in hijack_values:
        raise ValidationError("DNS leak audit requires UDP and TCP port-53 hijacking")
    if tun.get("auto-route") is not True:
        raise ValidationError("DNS leak audit requires tun auto-route")
    if tun.get("strict-route") is not True:
        raise ValidationError("DNS leak audit requires tun strict-route")
    if tun.get("auto-detect-interface") is not True:
        raise ValidationError("DNS leak audit requires tun auto-detect-interface")

    rule_providers = config.get("rule-providers", {})
    if not isinstance(rule_providers, dict):
        raise ValidationError("DNS leak audit requires rule-providers to be a mapping")
    policy = dns.get("nameserver-policy")
    if not isinstance(policy, dict) or not policy:
        raise ValidationError("DNS leak audit requires routing-derived nameserver-policy")

    for key, resolvers in policy.items():
        if not isinstance(key, str) or not key.startswith("rule-set:"):
            raise ValidationError("DNS leak audit forbids standalone DNS classification rules")
        provider = key.split(":", 1)[1]
        if provider not in rule_providers:
            raise ValidationError("DNS leak audit found an unknown DNS rule-provider reference")
        values = _resolver_values(resolvers)
        if not values or not all(_encrypted_resolver(item) for item in values):
            raise ValidationError("DNS leak audit requires encrypted nameserver-policy endpoints")

    return {
        "status": "passed",
        "mode": "strict_tun",
        "policy_rulesets": len(policy),
        "encrypted_resolver_fields": 4,
        "dns_hijack_protocols": 2,
    }
