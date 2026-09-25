"""Network profile compilation layer for deployment-environment adaptation.

A network profile adapts resolver pools and client URLTest tuning to the
network an operator actually deploys into, without touching renderer,
generator, or routing code. Profiles are declared data plus two explicit
compile passes:

1. ``apply_network_profile`` merges profile resolver pools into the public
   config model before rendering, so RuntimeConfigRenderer, DNS policy
   compilation, and every audit observe one effective configuration.
2. ``apply_network_profile_urltest`` layers profile URLTest tuning on the
   compiled group graph and fail-closes if the scheduling declarations or the
   compiled output no longer satisfy the profile contract.

Nothing outside this module may branch on profile names.
"""

from __future__ import annotations

from typing import Any

from .browsing_runtime import BROWSING_AUTO_GROUP
from .errors import ConfigurationError, GenerationError

NETWORK_PROFILE_DEFAULT = "default"
NETWORK_PROFILE_CN_THREE_NET = "cn_three_net"
_NETWORK_PROFILES = (NETWORK_PROFILE_DEFAULT, NETWORK_PROFILE_CN_THREE_NET)

_CANONICAL_PROBE_URL = "https://cp.cloudflare.com/generate_204"
_AI_GROUP_PREFIXES = ("AI ·", "__CR_AI_")

# The China three-network profile uses resolvers that answer consistently on
# China Telecom, China Unicom, and China Mobile access networks: domestic
# IP-literal UDP bootstrap, domestic DoH for normal resolution, and the OS
# resolver plus domestic DoH for proxy-server and DIRECT names so proxy
# hostnames follow carrier-local CDN answers instead of foreign anycast.
# The declared ``nameserver_policy_overrides`` (ACL4SSR rule-set routes and the
# exact ``stun.l.google.com`` override) and ``direct_nameserver_follow_policy:
# false`` are preserved untouched, so no wildcard keys are introduced and the
# resolver transport stays independent of proxy routing.
_CN_THREE_NET_DNS_OVERRIDES: dict[str, list[str]] = {
    "default_nameservers": ["223.5.5.5", "119.29.29.29"],
    "nameservers": [
        "https://dns.alidns.com/dns-query",
        "https://doh.pub/dns-query",
    ],
    "proxy_server_nameservers": [
        "system",
        "https://dns.alidns.com/dns-query",
        "https://doh.pub/dns-query",
    ],
    "direct_nameservers": [
        "system",
        "https://dns.alidns.com/dns-query",
        "https://doh.pub/dns-query",
    ],
}

# Client URLTest tuning. The client measures its own real access network; the
# profile never encodes carrier identities. Regional url-test groups widen the
# switch tolerance so carrier jitter cannot thrash selection, while the US
# keeps its historical tolerance. Browsing and regional intervals, timeouts,
# and max-failed-times match the canonical scheduling declarations and the
# qualification acceleration stage.
_CN_THREE_NET_BROWSING_PROBE = {"url": _CANONICAL_PROBE_URL, "interval": 180, "timeout": 8000}
_CN_THREE_NET_REGION_SWITCH_INTERVAL = 300
_CN_THREE_NET_REGIONAL_TOLERANCE_MS = {"HK": 120, "TW": 120, "SG": 120, "JP": 120, "KR": 120}
_CN_THREE_NET_US_TOLERANCE_MS = 150
_CN_THREE_NET_MAX_FAILED_TIMES = {"browsing": 1, "regional_and_other": 2}

# Pinned ACL4SSR manifest group ids (rules/acl4ssr.yaml). The profile tunes the
# pinned classification instead of redefining groups; omitted empty regional
# groups stay omitted.
_REGIONAL_GROUP_IDS = {
    "policy_hk": "HK",
    "policy_tw": "TW",
    "policy_sg": "SG",
    "policy_jp": "JP",
    "policy_kr": "KR",
    "policy_us": "US",
}
_DOWNLOAD_AUTO_GROUP_ID = "policy_download_auto"
_DOWNLOAD_MIN_INTERVAL = 600
_DOWNLOAD_MIN_TOLERANCE = 300


def resolve_network_profile(config: dict[str, Any]) -> str:
    """Return the declared runtime network profile."""

    runtime = config.get("runtime")
    profile = (
        runtime.get("network_profile", NETWORK_PROFILE_DEFAULT)
        if isinstance(runtime, dict)
        else NETWORK_PROFILE_DEFAULT
    )
    if profile not in _NETWORK_PROFILES:
        raise ConfigurationError(f"unsupported runtime.network_profile: {profile!r}")
    return str(profile)


def apply_network_profile(config: dict[str, Any]) -> dict[str, Any]:
    """Merge the declared profile's resolver pools into the config model.

    The override happens before rendering so every downstream consumer
    (renderer, DNS policy compilation, audits) sees one effective
    configuration. The default profile leaves the declared DNS untouched.
    """

    profile = resolve_network_profile(config)
    if profile == NETWORK_PROFILE_DEFAULT:
        return {
            "profile": NETWORK_PROFILE_DEFAULT,
            "status": "not_applicable",
            "dns_overrides": [],
            "urltest_tuning": False,
        }

    dns = config["runtime"]["dns"]
    if str(dns.get("mode", "managed")) != "managed" or dns.get("enabled") is not True:
        raise ConfigurationError(
            f"runtime.network_profile={NETWORK_PROFILE_CN_THREE_NET} requires enabled managed DNS"
        )
    for field, resolvers in _CN_THREE_NET_DNS_OVERRIDES.items():
        dns[field] = list(resolvers)
    return {
        "profile": NETWORK_PROFILE_CN_THREE_NET,
        "status": "applied",
        "dns_overrides": sorted(_CN_THREE_NET_DNS_OVERRIDES),
        "urltest_tuning": True,
    }


def _is_non_ai_automatic(group: dict[str, Any]) -> bool:
    name = group.get("name")
    if not isinstance(name, str) or name.startswith(_AI_GROUP_PREFIXES):
        return False
    return group.get("type") in {"url-test", "fallback"} and isinstance(group.get("url"), str)


def _probe_contract(policies: dict[str, Any]) -> dict[str, Any]:
    probes = policies.get("probes")
    browsing = probes.get("browsing") if isinstance(probes, dict) else None
    if not isinstance(browsing, dict):
        raise GenerationError("network profile cn_three_net requires the browsing probe")
    if browsing.get("url") != _CN_THREE_NET_BROWSING_PROBE["url"]:
        raise GenerationError(
            "network profile cn_three_net requires every client probe to use the canonical "
            "HTTPS Cloudflare generate_204 URL"
        )
    if browsing.get("interval") != _CN_THREE_NET_BROWSING_PROBE["interval"]:
        raise GenerationError("network profile cn_three_net requires the 180s browsing interval")
    if browsing.get("timeout") != _CN_THREE_NET_BROWSING_PROBE["timeout"]:
        raise GenerationError(
            "network profile cn_three_net requires the 8000ms browsing probe timeout"
        )
    scheduler = policies.get("scheduler")
    browsing_scheduler = scheduler.get("browsing") if isinstance(scheduler, dict) else None
    if not isinstance(browsing_scheduler, dict):
        raise GenerationError("network profile cn_three_net requires browsing scheduling")
    if browsing_scheduler.get("region_switch_interval") != _CN_THREE_NET_REGION_SWITCH_INTERVAL:
        raise GenerationError("network profile cn_three_net requires the 300s regional interval")
    return dict(_CN_THREE_NET_BROWSING_PROBE)


def _regional_overrides(
    output: dict[str, Any], group_specs: list[dict[str, Any]]
) -> dict[str, int]:
    groups = {
        str(group["name"]): group
        for group in output.get("proxy-groups", [])
        if isinstance(group, dict) and isinstance(group.get("name"), str)
    }
    tolerance_by_region = {
        **_CN_THREE_NET_REGIONAL_TOLERANCE_MS,
        "US": _CN_THREE_NET_US_TOLERANCE_MS,
    }
    applied: dict[str, int] = {}
    for spec in group_specs:
        region = _REGIONAL_GROUP_IDS.get(str(spec.get("id", "")))
        if region is None:
            continue
        group = groups.get(str(spec["display_name"]))
        if group is None:  # on_empty: omit keeps the group omitted
            continue
        if group.get("interval") != _CN_THREE_NET_REGION_SWITCH_INTERVAL:
            raise GenerationError(
                f"network profile cn_three_net requires regional group {spec['display_name']!r} "
                "to keep the 300s interval"
            )
        if group.get("timeout") != _CN_THREE_NET_BROWSING_PROBE["timeout"]:
            raise GenerationError(
                f"network profile cn_three_net requires regional group {spec['display_name']!r} "
                "to keep the 8000ms timeout"
            )
        tolerance = tolerance_by_region[region]
        group["tolerance"] = tolerance
        applied[str(spec["display_name"])] = tolerance
    if not applied:
        raise GenerationError("network profile cn_three_net found no regional groups to tune")
    return applied


def _download_contract(output: dict[str, Any], group_specs: list[dict[str, Any]]) -> None:
    groups = {
        str(group["name"]): group
        for group in output.get("proxy-groups", [])
        if isinstance(group, dict) and isinstance(group.get("name"), str)
    }
    for spec in group_specs:
        if str(spec.get("id", "")) != _DOWNLOAD_AUTO_GROUP_ID:
            continue
        group = groups.get(str(spec["display_name"]))
        if group is None:
            continue
        interval = group.get("interval")
        tolerance = group.get("tolerance")
        if not isinstance(interval, int) or interval < _DOWNLOAD_MIN_INTERVAL:
            raise GenerationError(
                "network profile cn_three_net requires the download group to stay low-frequency"
            )
        if not isinstance(tolerance, int) or tolerance < _DOWNLOAD_MIN_TOLERANCE:
            raise GenerationError(
                "network profile cn_three_net requires the download group to keep high tolerance"
            )


def apply_network_profile_urltest(
    output: dict[str, Any],
    *,
    config: dict[str, Any],
    policies: dict[str, Any],
    group_specs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Layer profile URLTest tuning on the compiled group graph.

    The default profile is a no-op. The China three-network profile verifies
    the client auto-selection contract (canonical HTTPS probe everywhere,
    browsing 180s/8000ms, regional 300s/8000ms, download low-frequency with
    high tolerance) and widens regional url-test tolerances so the client's
    own measurements decide node quality on jittery carrier access networks.
    """

    profile = resolve_network_profile(config)
    if profile == NETWORK_PROFILE_DEFAULT:
        return {"profile": NETWORK_PROFILE_DEFAULT, "status": "not_applicable"}

    probe = _probe_contract(policies)

    groups = output.get("proxy-groups", [])
    if not isinstance(groups, list):
        raise GenerationError("network profile tuning requires generated proxy-groups")
    automatic = [
        group for group in groups if isinstance(group, dict) and _is_non_ai_automatic(group)
    ]
    for group in automatic:
        if group.get("url") != _CANONICAL_PROBE_URL:
            raise GenerationError(
                "network profile cn_three_net requires every non-AI automatic group to use "
                "the canonical HTTPS Cloudflare generate_204 probe"
            )
    providers = output.get("proxy-providers", {})
    provider_count = 0
    if isinstance(providers, dict):
        for provider in providers.values():
            health_check = provider.get("health-check") if isinstance(provider, dict) else None
            if not isinstance(health_check, dict) or not health_check.get("enable"):
                continue
            provider_count += 1
            if health_check.get("url") != _CANONICAL_PROBE_URL:
                raise GenerationError(
                    "network profile cn_three_net requires every provider health check to use "
                    "the canonical HTTPS Cloudflare generate_204 probe"
                )

    automatic_by_name = {str(group.get("name")): group for group in automatic}
    regional_switch = automatic_by_name.get(BROWSING_AUTO_GROUP)
    if regional_switch is not None and regional_switch.get("interval") != (
        _CN_THREE_NET_REGION_SWITCH_INTERVAL
    ):
        raise GenerationError(
            "network profile cn_three_net requires the browsing regional switch to stay at 300s"
        )

    applied = _regional_overrides(output, group_specs)
    _download_contract(output, group_specs)

    return {
        "profile": NETWORK_PROFILE_CN_THREE_NET,
        "status": "applied",
        "browsing_probe": probe,
        "regional": {
            "interval": _CN_THREE_NET_REGION_SWITCH_INTERVAL,
            "timeout": _CN_THREE_NET_BROWSING_PROBE["timeout"],
            "tolerance_ms": {
                **dict(_CN_THREE_NET_REGIONAL_TOLERANCE_MS),
                "US": _CN_THREE_NET_US_TOLERANCE_MS,
            },
            "max_failed_times": dict(_CN_THREE_NET_MAX_FAILED_TIMES),
        },
        "tolerance_overrides": applied,
        "automatic_groups_checked": len(automatic),
        "provider_health_checks_checked": provider_count,
    }
