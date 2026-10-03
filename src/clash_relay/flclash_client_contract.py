"""Automated FlClash-facing configuration contract for Windows and Android clients."""

from __future__ import annotations

from typing import Any

from .config_loader import ProjectDefinition
from .errors import ValidationError
from .production_audit import _runtime_source_maps, audit_production_candidate
from .runtime_graph import RuntimeGraph

_PUBLIC_GROUPS = (
    "代理选择",
    "网页浏览",
    "人工智能",
    "流媒体",
    "消息通讯",
    "下载流量",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def audit_flclash_client_contract(
    project: ProjectDefinition,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Validate client-facing structure without claiming device/GUI execution."""

    production = audit_production_candidate(project, candidate)
    _require(
        production.get("status") == "passed",
        "FlClash contract requires production audit",
    )

    runtime_tun = project.config.get("runtime", {}).get("tun", {})
    _require(
        isinstance(runtime_tun, dict) and runtime_tun.get("mode") == "client",
        "FlClash contract requires client-owned TUN mode",
    )
    _require(
        "tun" not in candidate,
        "FlClash client-owned TUN must not be emitted by the generator",
    )

    profile = candidate.get("profile")
    _require(
        isinstance(profile, dict),
        "FlClash contract requires a Mihomo profile block",
    )
    _require(
        profile.get("store-fake-ip") is True,
        "FlClash contract requires fake-IP persistence",
    )

    dns = candidate.get("dns")
    _require(isinstance(dns, dict), "FlClash contract requires managed DNS")
    _require(dns.get("enable") is True, "FlClash contract requires DNS enabled")
    _require(
        dns.get("enhanced-mode") == "fake-ip",
        "FlClash contract requires fake-IP DNS",
    )
    _require(
        dns.get("direct-nameserver-follow-policy") is False,
        "FlClash contract requires explicit direct resolver bypass policy",
    )
    _require(
        isinstance(dns.get("direct-nameserver"), list)
        and bool(dns["direct-nameserver"]),
        "FlClash contract requires explicit direct resolver coverage",
    )
    _require(
        isinstance(dns.get("proxy-server-nameserver"), list)
        and bool(dns["proxy-server-nameserver"]),
        "FlClash contract requires proxy-server resolver coverage",
    )

    graph = RuntimeGraph.from_candidate(candidate)
    missing_groups = [name for name in _PUBLIC_GROUPS if name not in graph.groups]
    _require(
        not missing_groups,
        "FlClash contract is missing a required public selector",
    )

    urltest_groups = [
        row
        for row in candidate.get("proxy-groups", [])
        if isinstance(row, dict) and row.get("type") == "url-test"
    ]
    _require(bool(urltest_groups), "FlClash contract requires URLTest groups")
    _require(
        all(str(row.get("url", "")).startswith("https://") for row in urltest_groups),
        "FlClash URLTest groups must use HTTPS probes",
    )

    listeners = candidate.get("listeners")
    _require(
        isinstance(listeners, list),
        "FlClash contract requires the download listener",
    )
    download_listener = next(
        (row for row in listeners if isinstance(row, dict) and row.get("name") == "download-in"),
        None,
    )
    _require(
        isinstance(download_listener, dict)
        and download_listener.get("listen") == "127.0.0.1"
        and download_listener.get("proxy") == "下载流量",
        "FlClash contract requires the bound download-only listener",
    )

    subscriptions = {item.id for item in project.subscriptions if item.enabled}
    provider_sources, proxy_sources = _runtime_source_maps(
        graph,
        known_source_ids=subscriptions,
    )

    def reachable(target: str) -> frozenset[str]:
        return graph.reachable_sources(
            target,
            proxy_sources=proxy_sources,
            provider_sources=provider_sources,
            require_resolved=True,
        )

    general_sources = reachable("代理选择")
    web_sources = reachable("网页浏览")
    ai_sources = reachable("人工智能")
    streaming_sources = reachable("流媒体")
    messaging_sources = reachable("消息通讯")
    download_sources = reachable("下载流量")

    _require(
        "subscription_1" not in general_sources,
        "general routing can reach subscription_1",
    )
    _require(
        "subscription_1" not in streaming_sources,
        "streaming can reach subscription_1",
    )
    _require(
        "subscription_1" not in messaging_sources,
        "messaging can reach subscription_1",
    )
    _require(
        "subscription_1" not in download_sources,
        "download can reach subscription_1",
    )
    _require(
        ai_sources <= {"subscription_1"},
        "AI routing can reach a source outside subscription_1",
    )

    ai_resolution = graph.walk_resolved("人工智能")
    if not ai_sources:
        _require(
            ai_resolution.builtins == frozenset({"REJECT"}),
            "AI without subscription_1 inventory must fail closed",
        )

    checks = {
        "profile_structure": "passed",
        "tun_client_ownership": "passed",
        "managed_dns": "passed",
        "fake_ip_compatibility": "passed",
        "six_public_groups": "passed",
        "urltest": "passed",
        "source_isolation": "passed",
        "download_listener": "passed",
        "direct_dns_bypass": "passed",
    }
    scenarios = {
        "direct": "config_passed",
        "web_browsing": "config_passed",
        "ai": "config_passed",
        "streaming": "config_passed",
        "messaging": "config_passed",
        "download": "config_passed",
    }
    profiles = {
        "windows_system_proxy": "compatible_by_contract",
        "windows_tun": "compatible_by_contract",
        "android_vpn": "compatible_by_contract",
    }
    return {
        "schema_version": 1,
        "status": "passed",
        "automation_scope": "generated_config_contract",
        "mihomo_core_validation": "required_release_stage",
        "device_evidence": "required",
        "device_evidence_status": "unverified",
        "profiles": profiles,
        "checks": checks,
        "scenarios": scenarios,
        "source_reachability": {
            "general_sources": len(general_sources),
            "web_sources": len(web_sources),
            "ai_sources": len(ai_sources),
            "streaming_sources": len(streaming_sources),
            "messaging_sources": len(messaging_sources),
            "download_sources": len(download_sources),
            "subscription_1_download_reachable": False,
            "subscription_1_general_reachable": False,
        },
        "urltest_groups": len(urltest_groups),
        "direct_nameservers": len(dns["direct-nameserver"]),
    }
