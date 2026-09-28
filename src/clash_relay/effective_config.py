"""Public view of declared and effective network profile configuration."""

from __future__ import annotations

from typing import Any

from .config_loader import ProjectDefinition
from .network_profile import public_urltest_contract

_DNS_POOLS = (
    "default_nameservers",
    "nameservers",
    "proxy_server_nameservers",
    "direct_nameservers",
)


def _dns_pools(config: dict[str, Any]) -> dict[str, list[str]]:
    runtime = config.get("runtime")
    dns = runtime.get("dns") if isinstance(runtime, dict) else None
    if not isinstance(dns, dict):
        return {}
    return {
        field: [str(value) for value in dns[field]]
        for field in _DNS_POOLS
        if isinstance(dns.get(field), list)
    }


def describe_effective_config(project: ProjectDefinition) -> dict[str, Any]:
    """Describe only validated public policy, without subscription or node data."""

    declared = project.declared_config if project.declared_config is not None else project.config
    declared_dns = _dns_pools(declared)
    effective_dns = _dns_pools(project.config)
    profile = str(project.network_profile.get("profile", "default"))
    overrides = [
        {
            "field": f"runtime.dns.{field}",
            "source": f"network_profile.{profile}",
        }
        for field in _DNS_POOLS
        if declared_dns.get(field) != effective_dns.get(field)
    ]
    urltest = public_urltest_contract(project.config, project.policies)
    return {
        "status": "ready",
        "scope": "public_declarations",
        "network_profile": profile,
        "declared": {"dns": declared_dns},
        "effective": {"dns": effective_dns, "urltest": urltest},
        "overrides": overrides,
    }
