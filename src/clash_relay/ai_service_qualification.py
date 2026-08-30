"""Service-aware post-processing for privately qualified AI egress nodes."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .ai_qualification import AI_POLICY_GROUP, AI_PROVIDER_PREFIX, apply_ai_qualification
from .errors import ValidationError
from .util import atomic_write, dump_yaml, load_yaml_file
from .validator import validate_generated_config

_SERVICE_ORDER = ("ai_openai", "ai_claude", "ai_gemini")
_SERVICE_LABELS = {
    "ai_openai": "openai",
    "ai_claude": "claude",
    "ai_gemini": "gemini",
}
_SERVICE_TARGETS = {
    "ai_openai": "__CR_AI_SERVICE_OPENAI",
    "ai_claude": "__CR_AI_SERVICE_CLAUDE",
    "ai_gemini": "__CR_AI_SERVICE_GEMINI",
}
_CLAUDE_RULE_PROVIDER = "cr_ai_rules_claude"
_GEMINI_RULE_PROVIDER = "cr_ai_rules_gemini"
_ACL4SSR_AI_PROVIDER = "acl4ssr_ai"
_ACL4SSR_OPENAI_PROVIDER = "acl4ssr_openai"
_RE2_META = frozenset("\\.+*?()|[]{}^$")

# Exact subsets of the repository's pinned ACL4SSR AI.list. Keeping these exact
# makes a future upstream-ref update fail closed until its service routing is
# reviewed instead of silently widening classification.
_CLAUDE_AI_RULES = frozenset(
    {
        "DOMAIN-KEYWORD,anthropic",
        "DOMAIN-KEYWORD,claude",
        "DOMAIN-SUFFIX,anthropic.com",
        "DOMAIN-SUFFIX,claude.ai",
        "DOMAIN-SUFFIX,claude.com",
        "DOMAIN-SUFFIX,claudeusercontent.com",
    }
)
_GEMINI_AI_RULES = frozenset(
    {
        "DOMAIN,ai.google.dev",
        "DOMAIN,aistudio.google.com",
        "DOMAIN,bard.google.com",
        "DOMAIN,gemini.google.com",
        "DOMAIN,generativelanguage.googleapis.com",
        "DOMAIN,notebooklm.google.com",
        "DOMAIN-SUFFIX,bard.google.com",
        "DOMAIN-SUFFIX,gemini.google.com",
        "DOMAIN-SUFFIX,makersuite.google.com",
        "DOMAIN-SUFFIX,notebooklm.google",
        "DOMAIN-SUFFIX,notebooklm.google.com",
    }
)


def _comment_header(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def _provider_routes(
    groups: list[dict[str, Any]], provider_names: set[str]
) -> dict[str, tuple[str, str]]:
    """Return provider -> (hidden country anchor, public country group)."""
    anchor_by_provider: dict[str, str] = {}
    for group in groups:
        if not isinstance(group, dict) or not group.get("hidden", False):
            continue
        name = group.get("name")
        uses = group.get("use", [])
        if not isinstance(name, str) or not isinstance(uses, list):
            continue
        for provider_name in uses:
            provider_name = str(provider_name)
            if provider_name in provider_names and provider_name not in anchor_by_provider:
                anchor_by_provider[provider_name] = name

    routes: dict[str, tuple[str, str]] = {}
    for provider_name, anchor_name in anchor_by_provider.items():
        for group in groups:
            if not isinstance(group, dict) or group.get("hidden", False):
                continue
            if group.get("proxies") == [anchor_name] and isinstance(group.get("name"), str):
                routes[provider_name] = (anchor_name, str(group["name"]))
                break
    missing = provider_names - set(routes)
    if missing:
        raise ValidationError("AI qualification could not resolve every country provider route")
    return routes


def _quote_re2_literal(value: str) -> str:
    """Quote one runtime proxy name for Mihomo's Go/RE2-compatible regex parser."""
    return "".join(f"\\{character}" if character in _RE2_META else character for character in value)


def _exact_filter(names: set[str]) -> str:
    if not names:
        raise ValidationError("AI service filter cannot be empty")
    return "^(" + "|".join(_quote_re2_literal(name) for name in sorted(names)) + ")$"


def _service_country_anchor_name(service: str, provider_name: str) -> str:
    token = _SERVICE_LABELS[service].upper()
    digest = hashlib.sha256(provider_name.encode("utf-8")).hexdigest()[:10]
    return f"__CR_AI_{token}_{digest}"


def _clone_country_anchor(
    groups: list[dict[str, Any]],
    *,
    source_name: str,
    clone_name: str,
    names: set[str],
) -> dict[str, Any]:
    source = next(
        (
            group
            for group in groups
            if isinstance(group, dict) and str(group.get("name")) == source_name
        ),
        None,
    )
    if not isinstance(source, dict) or source.get("type") != "url-test":
        raise ValidationError("AI service routing requires a url-test country anchor")
    if source.get("filter"):
        raise ValidationError("AI service routing cannot compose an existing country filter")
    clone = dict(source)
    clone["name"] = clone_name
    clone["hidden"] = True
    clone["filter"] = _exact_filter(names)
    groups.append(clone)
    return clone


def _add_service_target(
    groups: list[dict[str, Any]],
    *,
    service: str,
    child_names: list[str],
    template: dict[str, Any] | None,
) -> None:
    target = _SERVICE_TARGETS[service]
    if not child_names:
        groups.append(
            {
                "name": target,
                "type": "select",
                "hidden": True,
                "proxies": ["REJECT"],
            }
        )
        return
    if len(child_names) == 1:
        groups.append(
            {
                "name": target,
                "type": "select",
                "hidden": True,
                "proxies": child_names,
            }
        )
        return
    if not isinstance(template, dict):
        raise ValidationError("AI service fallback requires a country probe template")
    group: dict[str, Any] = {
        "name": target,
        "type": "fallback",
        "hidden": True,
        "proxies": child_names,
    }
    for key in ("url", "interval", "timeout", "lazy", "expected-status"):
        if key not in template:
            raise ValidationError(f"AI service fallback template is missing {key!r}")
        group[key] = template[key]
    groups.append(group)


def _ruleset_line(rules: list[str], provider_name: str) -> tuple[int, str]:
    matches = [
        (index, rule)
        for index, rule in enumerate(rules)
        if isinstance(rule, str) and rule.startswith(f"RULE-SET,{provider_name},")
    ]
    if len(matches) != 1:
        raise ValidationError(f"AI service routing requires exactly one {provider_name!r} rule")
    return matches[0]


def _rewrite_service_rules(config: dict[str, Any]) -> dict[str, int]:
    rule_providers = config.get("rule-providers")
    rules = config.get("rules")
    if not isinstance(rule_providers, dict) or not isinstance(rules, list):
        raise ValidationError("AI service routing requires generated ACL4SSR rule providers")

    ai_provider = rule_providers.get(_ACL4SSR_AI_PROVIDER)
    if not isinstance(ai_provider, dict) or not isinstance(ai_provider.get("payload"), list):
        raise ValidationError("AI service routing requires the pinned ACL4SSR AI provider")
    openai_provider = rule_providers.get(_ACL4SSR_OPENAI_PROVIDER)
    if not isinstance(openai_provider, dict) or not isinstance(
        openai_provider.get("payload"), list
    ):
        raise ValidationError("AI service routing requires the pinned ACL4SSR OpenAI provider")

    ai_payload = {str(rule) for rule in ai_provider["payload"]}
    missing_claude = _CLAUDE_AI_RULES - ai_payload
    missing_gemini = _GEMINI_AI_RULES - ai_payload
    if missing_claude or missing_gemini:
        raise ValidationError("pinned ACL4SSR AI rules changed; service routing requires review")

    rule_providers[_CLAUDE_RULE_PROVIDER] = {
        "type": "inline",
        "behavior": "classical",
        "payload": sorted(_CLAUDE_AI_RULES),
    }
    rule_providers[_GEMINI_RULE_PROVIDER] = {
        "type": "inline",
        "behavior": "classical",
        "payload": sorted(_GEMINI_AI_RULES),
    }

    ai_index, ai_rule = _ruleset_line(rules, _ACL4SSR_AI_PROVIDER)
    openai_index, _ = _ruleset_line(rules, _ACL4SSR_OPENAI_PROVIDER)
    if ai_rule.split(",", 2)[2] != AI_POLICY_GROUP:
        raise ValidationError("generic ACL4SSR AI rule no longer targets the AI policy group")

    # Remove the dedicated OpenAI rule from its old position first so index
    # arithmetic cannot accidentally leave a generic AI rule ahead of it.
    rules.pop(openai_index)
    ai_index, _ = _ruleset_line(rules, _ACL4SSR_AI_PROVIDER)
    service_rules = [
        f"RULE-SET,{_ACL4SSR_OPENAI_PROVIDER},{_SERVICE_TARGETS['ai_openai']}",
        f"RULE-SET,{_CLAUDE_RULE_PROVIDER},{_SERVICE_TARGETS['ai_claude']}",
        f"RULE-SET,{_GEMINI_RULE_PROVIDER},{_SERVICE_TARGETS['ai_gemini']}",
    ]
    rules[ai_index:ai_index] = service_rules
    return {
        "openai_rules": len(openai_provider["payload"]),
        "claude_rules": len(_CLAUDE_AI_RULES),
        "gemini_rules": len(_GEMINI_AI_RULES),
    }


def apply_ai_service_qualification(
    config: dict[str, Any],
    qualified_by_probe: dict[str, set[str]],
) -> dict[str, Any]:
    """Route each AI service only through nodes that passed that service's probe."""
    missing = set(_SERVICE_ORDER) - set(qualified_by_probe)
    if missing:
        raise ValidationError("AI service qualification is missing required probe results")

    providers = config.get("proxy-providers")
    groups = config.get("proxy-groups")
    if not isinstance(providers, dict) or not isinstance(groups, list):
        raise ValidationError("candidate proxy provider/group structure is invalid")
    ai_provider_names = {
        str(name) for name in providers if str(name).startswith(AI_PROVIDER_PREFIX)
    }
    if not ai_provider_names:
        raise ValidationError("candidate contains no AI country providers")

    routes = _provider_routes(groups, ai_provider_names)
    original_names_by_provider: dict[str, set[str]] = {}
    for provider_name in ai_provider_names:
        provider = providers.get(provider_name)
        payload = provider.get("payload") if isinstance(provider, dict) else None
        if not isinstance(payload, list):
            raise ValidationError("AI provider payload is invalid")
        original_names_by_provider[provider_name] = {
            str(proxy["name"])
            for proxy in payload
            if isinstance(proxy, dict) and isinstance(proxy.get("name"), str)
        }

    candidate_names = set().union(*original_names_by_provider.values())
    for service in _SERVICE_ORDER:
        unknown = set(qualified_by_probe[service]) - candidate_names
        if unknown:
            raise ValidationError("AI service qualification returned unknown candidate nodes")

    service_names_by_provider: dict[str, dict[str, set[str]]] = {
        service: {
            provider_name: original_names_by_provider[provider_name]
            & set(qualified_by_probe[service])
            for provider_name in ai_provider_names
        }
        for service in _SERVICE_ORDER
    }
    union_names = set().union(*(set(qualified_by_probe[service]) for service in _SERVICE_ORDER))
    if not union_names:
        raise ValidationError(
            "no nodes passed any AI service qualification probe; refusing to replace the published profile"
        )

    # Reuse the existing country-pool pruning logic, but keep the union of
    # service-qualified nodes instead of requiring one node to pass all services.
    base_report = apply_ai_qualification(config, union_names)

    groups = config["proxy-groups"]
    retained_providers = set(config["proxy-providers"])
    ai_policy = next(
        (
            group
            for group in groups
            if isinstance(group, dict)
            and not group.get("hidden", False)
            and group.get("name") == AI_POLICY_GROUP
        ),
        None,
    )
    if not isinstance(ai_policy, dict) or not isinstance(ai_policy.get("proxies"), list):
        raise ValidationError("AI policy group is missing after qualification")
    country_order = [str(name) for name in ai_policy["proxies"] if str(name) != "DIRECT"]
    provider_by_public = {
        public_name: provider_name
        for provider_name, (_, public_name) in routes.items()
        if provider_name in retained_providers
    }

    service_country_counts: dict[str, dict[str, int]] = {}
    service_counts: dict[str, int] = {}
    failed_closed: list[str] = []
    for service in _SERVICE_ORDER:
        label = _SERVICE_LABELS[service]
        service_counts[label] = len(qualified_by_probe[service])
        service_country_counts[label] = {
            public_name: len(service_names_by_provider[service].get(provider_name, set()))
            for public_name, provider_name in provider_by_public.items()
        }
        child_names: list[str] = []
        template: dict[str, Any] | None = None
        for public_name in country_order:
            provider_name = provider_by_public.get(public_name)
            if provider_name is None:
                continue
            names = service_names_by_provider[service].get(provider_name, set())
            if not names:
                continue
            source_anchor, _ = routes[provider_name]
            clone_name = _service_country_anchor_name(service, provider_name)
            clone = _clone_country_anchor(
                groups,
                source_name=source_anchor,
                clone_name=clone_name,
                names=names,
            )
            child_names.append(clone_name)
            template = template or clone
        _add_service_target(
            groups,
            service=service,
            child_names=child_names,
            template=template,
        )
        if not child_names:
            failed_closed.append(label)

    routing_report = _rewrite_service_rules(config)
    validate_generated_config(config)
    return {
        "qualification_mode": "per-service",
        "tested_nodes": base_report["tested_nodes"],
        "qualified_nodes": base_report["qualified_nodes"],
        "country_groups": base_report["country_groups"],
        "removed_country_groups": base_report["removed_country_groups"],
        "service_qualified_nodes": service_counts,
        "service_country_groups": service_country_counts,
        "service_fail_closed": failed_closed,
        "service_rules": routing_report,
    }


def rewrite_ai_service_qualified_candidate(
    candidate_path: Path,
    qualified_by_probe: dict[str, set[str]],
) -> dict[str, Any]:
    try:
        original = candidate_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError("failed to read candidate for AI service qualification") from exc
    config = load_yaml_file(candidate_path)
    if not isinstance(config, dict):
        raise ValidationError("candidate is not a YAML mapping")
    report = apply_ai_service_qualification(config, qualified_by_probe)
    atomic_write(candidate_path, _comment_header(original) + dump_yaml(config))
    return report
