"""Static validation of generated Mihomo configuration."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .errors import ValidationError
from .schema import validate_schema
from .status import parse_expected_status
from .util import stable_json

_BUILTINS = {"DIRECT", "REJECT", "PASS", "COMPATIBLE"}
_FORBIDDEN_TOP_LEVEL = {
    "external-controller",
    "external-controller-tls",
    "secret",
    "authentication",
    "listeners",
    "tunnels",
}
_SAFE_SHARED_ANCHOR_PREFIXES = ("__CR_AUTO_", "__CR_FALLBACK_", "__CR_FAIL_CLOSED_")


def _cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []
    found: list[list[str]] = []

    def visit(node: str) -> None:
        if node in visiting:
            try:
                start = stack.index(node)
            except ValueError:
                start = 0
            found.append([*stack[start:], node])
            return
        if node in visited:
            return
        visiting.add(node)
        stack.append(node)
        for child in sorted(graph.get(node, set())):
            visit(child)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for node in sorted(graph):
        visit(node)
    return found


def _rule_target(rule: str) -> str:
    parts = rule.split(",")
    if len(parts) < 2:
        raise ValidationError(f"invalid generated rule: {rule!r}")
    if parts[0] == "MATCH":
        if len(parts) != 2:
            raise ValidationError(f"invalid MATCH rule: {rule!r}")
        return parts[1]
    if len(parts) < 3:
        raise ValidationError(f"generated rule has no target: {rule!r}")
    return parts[2]


def _reachable_providers(anchor: str, groups: dict[str, dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    pending = [anchor]
    visited: set[str] = set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        group = groups.get(name)
        if not isinstance(group, dict):
            continue
        uses = group.get("use", [])
        if isinstance(uses, list):
            found.update(item for item in uses if isinstance(item, str))
        references = group.get("proxies", [])
        if isinstance(references, list):
            pending.extend(item for item in references if isinstance(item, str) and item in groups)
    return found


def validate_generated_config(config: dict[str, Any], *, secret_urls: tuple[str, ...] = ()) -> None:
    validate_schema(config, "mihomo-output.schema.json", source="generated config", output=True)
    errors: list[str] = []
    if "proxies" in config:
        errors.append("top-level raw proxies are forbidden; use inline providers")
    forbidden = sorted(_FORBIDDEN_TOP_LEVEL & set(config))
    if forbidden:
        errors.append(f"forbidden private/control fields are present: {forbidden}")

    providers = config.get("proxy-providers", {})
    rule_providers = config.get("rule-providers", {})
    groups = config.get("proxy-groups", [])
    if not isinstance(providers, dict):
        errors.append("proxy-providers must be a mapping")
        providers = {}
    if not isinstance(rule_providers, dict):
        errors.append("rule-providers must be a mapping")
        rule_providers = {}
    if not isinstance(groups, list):
        errors.append("proxy-groups must be a list")
        groups = []

    provider_proxy_names: set[str] = set()
    provider_proxy_owner: dict[str, str] = {}
    for provider_name, provider in providers.items():
        if not isinstance(provider_name, str) or not provider_name:
            errors.append("provider names must be non-empty strings")
            continue
        if not isinstance(provider, dict):
            errors.append(f"provider {provider_name!r} is not a mapping")
            continue
        if provider.get("type") != "inline":
            errors.append(f"provider {provider_name!r} is not inline")
        if "url" in provider or "path" in provider:
            errors.append(f"provider {provider_name!r} contains an external URL/path")
        payload = provider.get("payload")
        if not isinstance(payload, list) or not payload:
            errors.append(f"provider {provider_name!r} is empty")
            continue
        health = provider.get("health-check")
        if not isinstance(health, dict) or health.get("enable") is not True:
            errors.append(f"provider {provider_name!r} has no enabled health-check")
        else:
            if not isinstance(health.get("url"), str) or not health["url"].startswith("https://"):
                errors.append(f"provider {provider_name!r} health-check URL is not HTTPS")
            try:
                parse_expected_status(str(health.get("expected-status", "")))
            except Exception as exc:  # converted to a static validation error below
                errors.append(f"provider {provider_name!r} has invalid expected-status: {exc}")
        for proxy in payload:
            if not isinstance(proxy, dict):
                errors.append(f"provider {provider_name!r} contains a non-mapping proxy")
                continue
            name = proxy.get("name")
            if not isinstance(name, str) or not name:
                errors.append(f"provider {provider_name!r} contains an unnamed proxy")
                continue
            if name in provider_proxy_names:
                errors.append(
                    f"runtime proxy name {name!r} is shared by providers "
                    f"{provider_proxy_owner[name]!r} and {provider_name!r}"
                )
            provider_proxy_names.add(name)
            provider_proxy_owner[name] = provider_name
            dialer = proxy.get("dialer-proxy")
            if dialer is not None:
                if not provider_name.startswith("cr_chain_exit_"):
                    errors.append(
                        f"provider {provider_name!r} has uncontrolled dialer-proxy injection"
                    )
                if not isinstance(dialer, str) or not dialer.startswith("__CR_CHAIN_ENTRY_AUTO_"):
                    errors.append(
                        f"provider {provider_name!r} dialer-proxy does not reference a controlled chain entry"
                    )

    for provider_name, provider in rule_providers.items():
        if not isinstance(provider_name, str) or not provider_name:
            errors.append("rule provider names must be non-empty strings")
            continue
        if not isinstance(provider, dict):
            errors.append(f"rule provider {provider_name!r} is not a mapping")
            continue
        if provider.get("type") != "inline":
            errors.append(f"rule provider {provider_name!r} is not inline")
        if provider.get("behavior") != "classical":
            errors.append(f"rule provider {provider_name!r} is not classical")
        if "url" in provider or "path" in provider:
            errors.append(f"rule provider {provider_name!r} contains an external URL/path")
        payload = provider.get("payload")
        if not isinstance(payload, list) or not payload:
            errors.append(f"rule provider {provider_name!r} is empty")
            continue
        for rule in payload:
            if not isinstance(rule, str) or not rule.strip() or "," not in rule:
                errors.append(f"rule provider {provider_name!r} contains an invalid classical rule")
                break

    group_names: set[str] = set()
    group_rows: dict[str, dict[str, Any]] = {}
    for group in groups:
        if not isinstance(group, dict):
            errors.append("proxy group entry must be a mapping")
            continue
        name = group.get("name")
        if not isinstance(name, str) or not name:
            errors.append("proxy group has no name")
            continue
        if name in group_names:
            errors.append(f"duplicate proxy group name: {name!r}")
        group_names.add(name)
        group_rows[name] = group

    hidden_names = {name for name, group in group_rows.items() if bool(group.get("hidden", False))}
    nested_hidden_names = {name for name in hidden_names if not name.startswith("__CR_")}
    graph: dict[str, set[str]] = defaultdict(set)
    visible_parents: dict[str, list[str]] = defaultdict(list)
    for name, group in group_rows.items():
        references = group.get("proxies", [])
        if references is not None and not isinstance(references, list):
            errors.append(f"group {name!r} proxies must be a list")
            references = []
        for reference in references or []:
            if reference in group_names:
                graph[name].add(reference)
            elif reference not in _BUILTINS:
                errors.append(f"group {name!r} references unknown proxy/group {reference!r}")
        uses = group.get("use", [])
        if uses is not None and not isinstance(uses, list):
            errors.append(f"group {name!r} use must be a list")
            uses = []
        for provider_name in uses or []:
            if provider_name not in providers:
                errors.append(f"group {name!r} references unknown provider {provider_name!r}")
        if group.get("type") in {"url-test", "fallback"}:
            try:
                parse_expected_status(str(group.get("expected-status", "")))
            except Exception as exc:
                errors.append(f"group {name!r} has invalid expected-status: {exc}")

        if not group.get("hidden", False):
            if group.get("type") != "select":
                errors.append(f"public group {name!r} must be a select group")
            public_refs = group.get("proxies", [])
            if not isinstance(public_refs, list) or not public_refs:
                errors.append(f"public group {name!r} must have at least one proxy/group reference")
                public_refs = []

            for reference in public_refs:
                if reference in nested_hidden_names:
                    visible_parents[str(reference)].append(name)

            provider_backed = bool(uses)
            if provider_backed:
                if len(public_refs) != 1 or public_refs[0] not in hidden_names:
                    errors.append(
                        f"provider-backed public group {name!r} must point only to one hidden "
                        "routing anchor"
                    )
                else:
                    reachable = _reachable_providers(str(public_refs[0]), group_rows)
                    if set(uses) != reachable:
                        errors.append(
                            f"provider-backed public group {name!r} exposes providers outside "
                            "its routing anchor"
                        )
            else:
                hidden_refs = [reference for reference in public_refs if reference in hidden_names]
                unsafe_hidden_refs = [
                    reference
                    for reference in hidden_refs
                    if reference not in nested_hidden_names
                    and not str(reference).startswith(_SAFE_SHARED_ANCHOR_PREFIXES)
                ]
                if unsafe_hidden_refs:
                    errors.append(
                        f"policy-only public group {name!r} references forbidden internal groups: "
                        f"{unsafe_hidden_refs}"
                    )
        elif not name.startswith("__CR_"):
            nested_refs = group.get("proxies", [])
            valid_nested_selector = (
                group.get("type") == "select"
                and not uses
                and isinstance(nested_refs, list)
                and len(nested_refs) == 1
                and nested_refs[0] in hidden_names
                and str(nested_refs[0]).startswith(_SAFE_SHARED_ANCHOR_PREFIXES)
            )
            if not valid_nested_selector:
                errors.append(f"hidden internal group {name!r} lacks the reserved __CR_ prefix")

    for name in sorted(nested_hidden_names):
        parents = visible_parents.get(name, [])
        if len(parents) != 1:
            errors.append(
                f"nested hidden group {name!r} must be referenced by exactly one public parent group"
            )

    for provider_name, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        for proxy in provider.get("payload", []):
            if (
                isinstance(proxy, dict)
                and "dialer-proxy" in proxy
                and proxy["dialer-proxy"] not in group_names
            ):
                errors.append(
                    f"provider {provider_name!r} dialer-proxy references an unknown group"
                )

    cycle_list = _cycles(dict(graph))
    if cycle_list:
        errors.append(f"proxy group reference cycle detected: {cycle_list[0]}")

    rules = config.get("rules", [])
    if not isinstance(rules, list) or not rules:
        errors.append("rules must be a non-empty list")
    else:
        if not isinstance(rules[-1], str) or not rules[-1].startswith("MATCH,"):
            errors.append("the final rule must be MATCH")
        if len(rules) != len(set(rules)):
            errors.append("generated rules contain exact duplicates")
        for rule in rules:
            if not isinstance(rule, str):
                errors.append("generated rules must be strings")
                continue
            parts = rule.split(",")
            if parts[0] == "RULE-SET" and (len(parts) < 3 or parts[1] not in rule_providers):
                errors.append(f"RULE-SET references unknown rule provider in {rule!r}")
                continue
            try:
                target = _rule_target(rule)
            except ValidationError as exc:
                errors.append(str(exc))
                continue
            if target not in group_names and target not in _BUILTINS:
                errors.append(f"rule references unknown target {target!r}")
            elif target in nested_hidden_names:
                errors.append(
                    f"rule targets nested hidden group {target!r}; target its public parent instead"
                )

    serialized = stable_json(config)
    for value in secret_urls:
        if value and value in serialized:
            errors.append("a subscription URL secret leaked into the generated configuration")
            break
    if errors:
        raise ValidationError("generated configuration is invalid: " + "; ".join(errors[:30]))
