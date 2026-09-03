"""Declarative names and fidelity contracts for the canonical production profile.

The production compiler and audits consume ``routing.contract`` as the sole
source of runtime selector names, compatibility bindings, AI aliases, and
classification ordering.  A legacy fallback remains only for projects that do
not declare Routing V2 at all; once a ``routing`` block is present the contract
is mandatory and fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import ConfigurationError

# Compatibility only.  Canonical/production projects declare routing.contract
# explicitly in policies.yaml.  Do not add new production semantics here.
_LEGACY_DEFAULT_CONTRACT: dict[str, Any] = {
    "public_groups": {
        "general": "代理选择",
        "browsing": "网页浏览",
        "ai": "人工智能",
        "media": "流媒体",
        "messaging": "消息通讯",
        "download": "下载流量",
    },
    "automatic_groups": {
        "media": "媒体自动",
        "messaging": "通讯自动",
        "download": "下载自动",
    },
    "general_region_choices": [
        "香港节点",
        "台湾节点",
        "新加坡节点",
        "日本节点",
        "美国节点",
        "韩国节点",
        "DIRECT",
    ],
    "compatibility_selectors": {
        "全球直连": ["DIRECT", "代理选择", "自动选择"],
        "广告拦截": ["REJECT", "DIRECT"],
        "谷歌FCM": ["代理选择", "全球直连", "自动选择"],
        "微软服务": ["全球直连", "代理选择"],
        "苹果服务": ["代理选择", "全球直连"],
        "漏网之鱼": ["代理选择", "全球直连", "自动选择"],
    },
    "disabled_groups": ["应用净化"],
    "ai": {
        "service_targets": {
            "openai": "__CR_AI_SERVICE_OPENAI",
            "claude": "__CR_AI_SERVICE_CLAUDE",
            "gemini": "__CR_AI_SERVICE_GEMINI",
        },
        "service_prefixes": {
            "openai": "__CR_AI_OPENAI_",
            "claude": "__CR_AI_CLAUDE_",
            "gemini": "__CR_AI_GEMINI_",
        },
        "region_display_names": {
            "HK": ["AI · 香港", "AI · HK"],
            "TW": ["AI · 台湾", "AI · TW"],
            "SG": ["AI · 新加坡", "AI · SG"],
            "JP": ["AI · 日本", "AI · JP"],
            "US": ["AI · 美国", "AI · US"],
            "KR": ["AI · 韩国", "AI · KR"],
            "OTHER": ["AI · 其他地区", "AI · OTHER"],
        },
        "required_excluded_regions": ["HK"],
    },
    "binding_targets": {
        "telegram": "消息通讯",
        "ai": "人工智能",
        "openai": "人工智能",
        "proxy_media": "流媒体",
        "download": "下载流量",
        "proxy_lite": "网页浏览",
        "china_domain": "全球直连",
        "china_company_ip": "全球直连",
        "geoip_cn": "全球直连",
    },
    "priority_edges": [
        ["telegram", "ai"],
        ["telegram", "openai"],
        ["ai", "proxy_media"],
        ["openai", "proxy_media"],
        ["proxy_media", "download"],
        ["download", "proxy_lite"],
        ["proxy_lite", "china_domain"],
        ["china_domain", "china_company_ip"],
        ["china_company_ip", "geoip_cn"],
    ],
    "acl4ssr_baseline": "ProxyMedia -> ProxyLite -> ChinaDomain -> ChinaCompanyIp -> GEOIP CN",
    "intentional_deviations": ["BanProgramAD disabled", "AI extension", "Download extension"],
}


@dataclass(frozen=True, slots=True)
class AiPolicyContract:
    service_targets: dict[str, str]
    service_prefixes: dict[str, str]
    region_display_names: dict[str, tuple[str, ...]]
    required_excluded_regions: tuple[str, ...]

    @property
    def canonical_region_display(self) -> dict[str, str]:
        return {region: names[0] for region, names in self.region_display_names.items() if names}

    @property
    def display_region_codes(self) -> dict[str, str]:
        return {
            display_name: region
            for region, display_names in self.region_display_names.items()
            for display_name in display_names
        }

    def region_for_display(self, display_name: str) -> str | None:
        return self.display_region_codes.get(display_name)


@dataclass(frozen=True, slots=True)
class RuntimePolicyContract:
    declared: bool
    public_groups: dict[str, str]
    automatic_groups: dict[str, str]
    general_region_choices: tuple[str, ...]
    compatibility_selectors: dict[str, tuple[str, ...]]
    disabled_groups: tuple[str, ...]
    ai: AiPolicyContract
    binding_targets: dict[str, str]
    priority_edges: tuple[tuple[str, str], ...]
    acl4ssr_baseline: str
    intentional_deviations: tuple[str, ...]

    @property
    def visible_groups(self) -> frozenset[str]:
        return frozenset(self.public_groups.values())

    def public_group(self, purpose: str) -> str:
        try:
            return self.public_groups[purpose]
        except KeyError as exc:
            raise ConfigurationError(
                f"routing contract has no public group for purpose {purpose!r}"
            ) from exc

    def automatic_group(self, purpose: str) -> str:
        try:
            return self.automatic_groups[purpose]
        except KeyError as exc:
            raise ConfigurationError(
                f"routing contract has no automatic group for purpose {purpose!r}"
            ) from exc

    def binding_target(self, source_id: str) -> str:
        try:
            return self.binding_targets[source_id]
        except KeyError as exc:
            raise ConfigurationError(
                f"routing contract has no target for binding {source_id!r}"
            ) from exc


def _string_mapping(value: Any, *, field: str) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise ConfigurationError(f"routing contract {field} must be a non-empty mapping")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or not isinstance(item, str) or not item:
            raise ConfigurationError(f"routing contract {field} must contain non-empty strings")
        result[key] = item
    return result


def _string_list(value: Any, *, field: str, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ConfigurationError(f"routing contract {field} must be a string list")
    items = tuple(str(item) for item in value if isinstance(item, str) and item)
    if len(items) != len(value) or len(items) != len(set(items)):
        raise ConfigurationError(f"routing contract {field} must contain unique non-empty strings")
    return items


def load_policy_contract(policies: dict[str, Any]) -> RuntimePolicyContract:
    routing = policies.get("routing")
    raw = routing.get("contract") if isinstance(routing, dict) else None
    if isinstance(routing, dict) and raw is None:
        raise ConfigurationError(
            "routing.contract is required whenever the routing policy is declared"
        )
    declared = raw is not None
    document = _LEGACY_DEFAULT_CONTRACT if raw is None else raw
    if not isinstance(document, dict):
        raise ConfigurationError("routing contract must be a mapping")

    public_groups = _string_mapping(document.get("public_groups"), field="public_groups")
    required_public = {"general", "browsing", "ai", "media", "messaging", "download"}
    if set(public_groups) != required_public:
        raise ConfigurationError(
            "routing contract public_groups must declare exactly: "
            + ", ".join(sorted(required_public))
        )

    automatic_groups = _string_mapping(document.get("automatic_groups"), field="automatic_groups")
    required_automatic = {"media", "messaging", "download"}
    if set(automatic_groups) != required_automatic:
        raise ConfigurationError(
            "routing contract automatic_groups must declare media, messaging, and download"
        )

    compatibility_raw = document.get("compatibility_selectors")
    if not isinstance(compatibility_raw, dict) or not compatibility_raw:
        raise ConfigurationError("routing contract compatibility_selectors must be a mapping")
    compatibility = {
        str(name): _string_list(members, field=f"compatibility_selectors.{name}")
        for name, members in compatibility_raw.items()
        if isinstance(name, str) and name
    }
    if len(compatibility) != len(compatibility_raw):
        raise ConfigurationError("routing contract compatibility selector names must be strings")

    ai_raw = document.get("ai")
    if not isinstance(ai_raw, dict):
        raise ConfigurationError("routing contract ai must be a mapping")
    region_raw = ai_raw.get("region_display_names")
    if not isinstance(region_raw, dict) or not region_raw:
        raise ConfigurationError("routing contract ai.region_display_names must be a mapping")
    region_display_names = {
        str(region): _string_list(names, field=f"ai.region_display_names.{region}")
        for region, names in region_raw.items()
        if isinstance(region, str) and region
    }
    if len(region_display_names) != len(region_raw):
        raise ConfigurationError("routing contract region names must be strings")

    all_region_displays = [
        display_name
        for display_names in region_display_names.values()
        for display_name in display_names
    ]
    if len(all_region_displays) != len(set(all_region_displays)):
        raise ConfigurationError(
            "routing contract AI region display aliases must be globally unique"
        )

    binding_targets = _string_mapping(document.get("binding_targets"), field="binding_targets")
    priority_raw = document.get("priority_edges")
    if not isinstance(priority_raw, list) or not priority_raw:
        raise ConfigurationError("routing contract priority_edges must be a non-empty list")
    priority_edges: list[tuple[str, str]] = []
    for edge in priority_raw:
        if (
            not isinstance(edge, list)
            or len(edge) != 2
            or not all(isinstance(item, str) and item for item in edge)
        ):
            raise ConfigurationError(
                "routing contract priority_edges entries must be [before, after]"
            )
        pair = (str(edge[0]), str(edge[1]))
        if pair[0] not in binding_targets or pair[1] not in binding_targets:
            raise ConfigurationError("routing contract priority_edges reference unknown bindings")
        priority_edges.append(pair)

    return RuntimePolicyContract(
        declared=declared,
        public_groups=public_groups,
        automatic_groups=automatic_groups,
        general_region_choices=_string_list(
            document.get("general_region_choices"), field="general_region_choices"
        ),
        compatibility_selectors=compatibility,
        disabled_groups=_string_list(
            document.get("disabled_groups", []), field="disabled_groups", allow_empty=True
        ),
        ai=AiPolicyContract(
            service_targets=_string_mapping(
                ai_raw.get("service_targets"), field="ai.service_targets"
            ),
            service_prefixes=_string_mapping(
                ai_raw.get("service_prefixes"), field="ai.service_prefixes"
            ),
            region_display_names=region_display_names,
            required_excluded_regions=_string_list(
                ai_raw.get("required_excluded_regions", []),
                field="ai.required_excluded_regions",
                allow_empty=True,
            ),
        ),
        binding_targets=binding_targets,
        priority_edges=tuple(priority_edges),
        acl4ssr_baseline=str(document.get("acl4ssr_baseline", "")),
        intentional_deviations=_string_list(
            document.get("intentional_deviations", []),
            field="intentional_deviations",
            allow_empty=True,
        ),
    )


def policy_contract_summary(contract: RuntimePolicyContract) -> dict[str, Any]:
    return {
        "declared": contract.declared,
        "public_groups": dict(contract.public_groups),
        "automatic_groups": dict(contract.automatic_groups),
        "required_ai_exclusions": list(contract.ai.required_excluded_regions),
        "compatibility_selectors": len(contract.compatibility_selectors),
        "bindings": len(contract.binding_targets),
        "priority_edges": len(contract.priority_edges),
    }
