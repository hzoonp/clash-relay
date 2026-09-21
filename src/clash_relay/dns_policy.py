"""Compile DNS resolver policy from the existing routing classification."""

from __future__ import annotations

from typing import Any

from .errors import GenerationError


def apply_dns_routing_policy(
    output: dict[str, Any],
    *,
    config: dict[str, Any],
    external_rules: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Bind ACL4SSR rule-provider classifications to resolver pools.

    The DNS policy deliberately reuses the same source scenarios that feed the
    main routing compiler. It does not maintain a second domain classification
    list.
    """

    dns_decl = config["runtime"]["dns"]
    policy_mode = str(dns_decl.get("routing_policy", "none"))
    if policy_mode == "none":
        return {"status": "not_applicable", "mode": "none"}
    if policy_mode != "acl4ssr":
        raise GenerationError(f"unsupported DNS routing policy mode: {policy_mode!r}")

    dns = output.get("dns")
    if not isinstance(dns, dict) or dns.get("enable") is not True:
        raise GenerationError("ACL4SSR DNS routing policy requires enabled managed DNS")

    rule_providers = output.get("rule-providers", {})
    if not isinstance(rule_providers, dict) or not rule_providers:
        raise GenerationError("ACL4SSR DNS routing policy requires generated rule providers")

    direct_resolvers = list(dns_decl.get("direct_nameservers", []))
    proxy_resolvers = list(dns_decl.get("nameservers", []))
    if not direct_resolvers or not proxy_resolvers:
        raise GenerationError("DNS routing policy requires direct and proxy resolver pools")

    policy: dict[str, list[str]] = {}
    direct_rulesets = 0
    proxy_rulesets = 0
    for directive in external_rules or []:
        provider = directive.get("provider")
        if not isinstance(provider, str) or not provider:
            continue
        if provider not in rule_providers:
            raise GenerationError("DNS routing policy references an unavailable rule provider")
        scenario = directive.get("scenario")
        if not isinstance(scenario, str) or not scenario:
            raise GenerationError(
                f"DNS routing policy requires scenario metadata for rule provider {provider!r}"
            )
        key = f"rule-set:{provider}"
        if key in policy:
            continue
        if scenario == "direct":
            policy[key] = list(direct_resolvers)
            direct_rulesets += 1
        else:
            policy[key] = list(proxy_resolvers)
            proxy_rulesets += 1

    if not policy:
        raise GenerationError("DNS routing policy produced no rule-set bindings")

    dns["nameserver-policy"] = policy
    output["dns"] = dns
    return {
        "status": "compiled",
        "mode": "acl4ssr",
        "direct_rulesets": direct_rulesets,
        "proxy_rulesets": proxy_rulesets,
        "total_rulesets": len(policy),
    }
