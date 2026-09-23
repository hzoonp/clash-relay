"""Validate that managed DNS control traffic cannot depend on proxy health checks."""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

from .errors import ValidationError


def _ip_literal_encrypted_resolver(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "tls", "quic"} or not parsed.hostname:
        return False
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return False
    return True


def audit_dns_runtime_dependencies(candidate: dict[str, Any]) -> dict[str, object]:
    """Fail closed on DNS-to-routing-to-URLTest dependency cycles.

    ``nameserver-policy`` still chooses a resolver for application domains. The
    resolver transport itself must remain independent from routing groups so a
    cold URLTest cannot be required to resolve its own check endpoint.
    """

    dns = candidate.get("dns")
    if not isinstance(dns, dict) or dns.get("enable") is not True:
        return {"status": "not_applicable"}
    if dns.get("enhanced-mode") != "fake-ip":
        raise ValidationError("managed DNS runtime requires fake-ip mode")
    if dns.get("respect-rules") is not False:
        raise ValidationError("DNS resolver transport must not respect proxy routing rules")
    if dns.get("direct-nameserver-follow-policy") is not False:
        raise ValidationError("DIRECT resolver transport must bypass nameserver policy")
    for field in ("default-nameserver", "proxy-server-nameserver"):
        resolvers = dns.get(field)
        if not isinstance(resolvers, list) or not resolvers:
            raise ValidationError(f"managed DNS runtime requires non-empty {field}")
        if not all(_ip_literal_encrypted_resolver(item) for item in resolvers):
            raise ValidationError(f"{field} must contain IP-literal encrypted resolvers")
    policy = dns.get("nameserver-policy")
    if not isinstance(policy, dict) or not policy:
        raise ValidationError("managed DNS runtime requires nameserver-policy")
    groups = candidate.get("proxy-groups", [])
    if not isinstance(groups, list):
        raise ValidationError("managed DNS runtime requires proxy groups")
    automatic = 0
    for group in groups:
        if not isinstance(group, dict) or group.get("type") not in {"url-test", "fallback"}:
            continue
        automatic += 1
        url = group.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ValidationError("automatic proxy groups must use HTTPS health-check URLs")
        if group.get("timeout") != 5000:
            raise ValidationError("automatic proxy groups must use a 5000ms health-check timeout")
    return {
        "status": "passed",
        "resolver_transport": "independent",
        "direct_resolver_policy": "bypass",
        "automatic_groups": automatic,
    }
