"""OpenAI/ChatGPT application routing and qualification contract.

The pinned ACL4SSR OpenAI list remains the upstream classification baseline.
This module owns the additional application surface that OpenAI documents for
ChatGPT web/desktop/mobile clients so that application traffic cannot silently
escape the service-qualified OpenAI route when upstream ACL data is narrower.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .util import atomic_write, dump_yaml, load_yaml_file
from .validator import validate_generated_config

CONTRACT_VERSION = 1
QUALIFICATION_VERSION = 1
SOURCE_URL = "https://help.openai.com/en/articles/9247338-network-recommendations-for-chatgpt-errors-on-web-and-apps"
SOURCE_REVIEWED = "2026-09-02"
RULE_PROVIDER = "cr_openai_app"
OPENAI_SERVICE_TARGET = "__CR_AI_SERVICE_OPENAI"
_ACL4SSR_OPENAI_PROVIDER = "acl4ssr_openai"
_ACL4SSR_AI_PROVIDER = "acl4ssr_ai"

# OpenAI-owned wildcard surfaces may safely use suffix rules. The third-party
# application dependencies below are intentionally exact-host rules unless the
# OpenAI allowlist itself requires a wildcard family. In particular, this
# contract must never route all of workos.com, cloudflare.com, stripe.com,
# sentry.io, datadoghq.com, apple.com, or imgix.net through the AI selector.
_OPENAI_SUFFIXES = (
    "auth.openai.com",
    "chatgpt.com",
    "ct.sendgrid.net",
    "intercom.io",
    "intercomcdn.com",
    "oaistatic.com",
    "oaiusercontent.com",
    "openai.com",
    "oaistatsig.com",
)
_EXACT_HOSTS = (
    "android.chat.openai.com",
    "auth0.openai.com",
    "cdn.openaimerge.com",
    "cdn.workos.com",
    "challenges.cloudflare.com",
    "chat.openai.com",
    "desktop.chat.openai.com",
    "desktop.chatgpt.com",
    "forwarder.workos.com",
    "humb.apple.com",
    "images.workoscdn.com",
    "ios.chat.openai.com",
    "js.intercomcdn.com",
    "js.stripe.com",
    "o207216.ingest.sentry.io",
    "o33249.ingest.sentry.io",
    "rum.browser-intake-datadoghq.com",
    "setup.auth.openai.com",
    "setup.workos.com",
    "tcr9i.chat.openai.com",
    "workos.imgix.net",
)

ROUTE_RULES = tuple(
    sorted(
        {
            *(f"DOMAIN-SUFFIX,{host}" for host in _OPENAI_SUFFIXES),
            *(f"DOMAIN,{host}" for host in _EXACT_HOSTS),
        }
    )
)

# The primary chatgpt.com probe remains declarative in policies.yaml. These
# additional endpoints make an OpenAI node App-ready rather than merely able to
# fetch one web page. 200-499 is deliberate for TLS-oriented endpoints: an
# application endpoint may reject HEAD or an unauthenticated request while its
# certificate/SNI path is still valid. The primary probe keeps its stricter
# policy-declared expected status.
_ADDITIONAL_CRITICAL_PROBES: tuple[dict[str, Any], ...] = (
    {
        "name": "openai_app_android",
        "url": "https://android.chat.openai.com/",
        "method": "HEAD",
        "expected_status": "200-499",
        "timeout": 5000,
    },
    {
        "name": "openai_app_auth",
        "url": "https://auth0.openai.com/",
        "method": "HEAD",
        "expected_status": "200-499",
        "timeout": 5000,
    },
    {
        "name": "openai_app_setup_auth",
        "url": "https://setup.auth.openai.com/",
        "method": "HEAD",
        "expected_status": "200-499",
        "timeout": 5000,
    },
)

# Supporting probes are diagnostic only. They are run for nodes that already
# passed every critical OpenAI probe and never turn an App-ready node into a
# failure merely because optional telemetry/CDN infrastructure is unavailable.
_SUPPORTING_PROBES: tuple[dict[str, Any], ...] = (
    {
        "name": "openai_support_workos",
        "url": "https://cdn.workos.com/",
        "method": "HEAD",
        "expected_status": "200-499",
        "timeout": 5000,
    },
    {
        "name": "openai_support_cloudflare",
        "url": "https://challenges.cloudflare.com/",
        "method": "HEAD",
        "expected_status": "200-499",
        "timeout": 5000,
    },
    {
        "name": "openai_support_merge_cdn",
        "url": "https://cdn.openaimerge.com/",
        "method": "HEAD",
        "expected_status": "200-499",
        "timeout": 5000,
    },
    {
        "name": "openai_support_datadog",
        "url": "https://rum.browser-intake-datadoghq.com/",
        "method": "HEAD",
        "expected_status": "200-499",
        "timeout": 5000,
    },
)

_FORBIDDEN_SHARED_SUFFIX_RULES = frozenset(
    {
        "DOMAIN-SUFFIX,workos.com",
        "DOMAIN-SUFFIX,cloudflare.com",
        "DOMAIN-SUFFIX,stripe.com",
        "DOMAIN-SUFFIX,sentry.io",
        "DOMAIN-SUFFIX,datadoghq.com",
        "DOMAIN-SUFFIX,apple.com",
        "DOMAIN-SUFFIX,imgix.net",
    }
)


def contract_fingerprint() -> str:
    """Return a deterministic fingerprint covering route and probe semantics."""
    document = {
        "contract_version": CONTRACT_VERSION,
        "qualification_version": QUALIFICATION_VERSION,
        "source_url": SOURCE_URL,
        "route_rules": ROUTE_RULES,
        "critical_probes": _ADDITIONAL_CRITICAL_PROBES,
        "supporting_probes": _SUPPORTING_PROBES,
        "semantics": "all-critical-pass;supporting-diagnostic-only;default-tls-verification",
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def cache_service_key(service: str) -> str:
    """Invalidate only OpenAI cache records when the App contract changes."""
    if service != "ai_openai":
        return service
    return f"{service}@{contract_fingerprint()[:16]}"


def critical_probes(primary: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    if str(primary.get("name")) != "ai_openai":
        raise ValidationError("OpenAI App qualification requires the ai_openai primary probe")
    if primary.get("method") != "HEAD" or not str(primary.get("url", "")).startswith("https://"):
        raise ValidationError("OpenAI App primary probe must be HTTPS HEAD")
    return (dict(primary), *(dict(probe) for probe in _ADDITIONAL_CRITICAL_PROBES))


def supporting_probes() -> tuple[dict[str, Any], ...]:
    return tuple(dict(probe) for probe in _SUPPORTING_PROBES)


def contract_summary() -> dict[str, Any]:
    return {
        "version": CONTRACT_VERSION,
        "qualification_version": QUALIFICATION_VERSION,
        "fingerprint": contract_fingerprint(),
        "source_reviewed": SOURCE_REVIEWED,
        "route_rules": len(ROUTE_RULES),
        "critical_endpoints": 1 + len(_ADDITIONAL_CRITICAL_PROBES),
        "supporting_endpoints": len(_SUPPORTING_PROBES),
    }


def _comment_header(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def _ruleset_indexes(rules: list[Any], provider: str) -> list[int]:
    prefix = f"RULE-SET,{provider},"
    return [
        index
        for index, rule in enumerate(rules)
        if isinstance(rule, str) and rule.startswith(prefix)
    ]


def apply_route_lock(config: dict[str, Any]) -> dict[str, Any]:
    """Route the documented ChatGPT App surface through OpenAI-qualified egress."""
    rule_providers = config.get("rule-providers")
    rules = config.get("rules")
    groups = config.get("proxy-groups")
    if (
        not isinstance(rule_providers, dict)
        or not isinstance(rules, list)
        or not isinstance(groups, list)
    ):
        raise ValidationError("OpenAI App route lock requires generated rule/group structures")
    if not any(
        isinstance(group, dict) and group.get("name") == OPENAI_SERVICE_TARGET for group in groups
    ):
        raise ValidationError("OpenAI App route lock requires the service-qualified OpenAI target")

    if _FORBIDDEN_SHARED_SUFFIX_RULES & set(ROUTE_RULES):
        raise ValidationError(
            "OpenAI App route contract contains an over-broad shared-infrastructure suffix"
        )

    acl_openai_indexes = _ruleset_indexes(rules, _ACL4SSR_OPENAI_PROVIDER)
    generic_ai_indexes = _ruleset_indexes(rules, _ACL4SSR_AI_PROVIDER)
    if len(acl_openai_indexes) != 1 or len(generic_ai_indexes) != 1:
        raise ValidationError(
            "OpenAI App route lock requires one ACL4SSR OpenAI and generic AI rule"
        )

    # Idempotent for recovery/testing: remove an existing lock before inserting
    # the canonical one immediately before the ACL4SSR OpenAI rule.
    rules[:] = [
        rule
        for rule in rules
        if not (isinstance(rule, str) and rule.startswith(f"RULE-SET,{RULE_PROVIDER},"))
    ]
    rule_providers[RULE_PROVIDER] = {
        "type": "inline",
        "behavior": "classical",
        "payload": list(ROUTE_RULES),
    }
    acl_openai_indexes = _ruleset_indexes(rules, _ACL4SSR_OPENAI_PROVIDER)
    if len(acl_openai_indexes) != 1:
        raise ValidationError("OpenAI App route lock lost the ACL4SSR OpenAI rule")
    insert_at = acl_openai_indexes[0]
    rules.insert(insert_at, f"RULE-SET,{RULE_PROVIDER},{OPENAI_SERVICE_TARGET}")

    report = audit_route_lock(config)
    report["status"] = "locked"
    return report


def audit_route_lock(config: dict[str, Any]) -> dict[str, Any]:
    """Fail closed if a post-qualification candidate can bypass the App route."""
    groups = config.get("proxy-groups")
    if not isinstance(groups, list):
        raise ValidationError("OpenAI App route audit requires proxy-groups")
    post_qualification = any(
        isinstance(group, dict) and group.get("name") == OPENAI_SERVICE_TARGET for group in groups
    )
    if not post_qualification:
        return {"status": "pre_qualification", **contract_summary()}

    rule_providers = config.get("rule-providers")
    rules = config.get("rules")
    if not isinstance(rule_providers, dict) or not isinstance(rules, list):
        raise ValidationError("OpenAI App route audit requires rule providers and rules")
    provider = rule_providers.get(RULE_PROVIDER)
    if not isinstance(provider, dict):
        raise ValidationError(
            "post-qualification candidate is missing the OpenAI App rule provider"
        )
    if provider.get("type") != "inline" or provider.get("behavior") != "classical":
        raise ValidationError("OpenAI App rule provider has unexpected runtime semantics")
    payload = provider.get("payload")
    if payload != list(ROUTE_RULES):
        raise ValidationError("OpenAI App rule provider drifted from the reviewed contract")

    lock_indexes = _ruleset_indexes(rules, RULE_PROVIDER)
    acl_openai_indexes = _ruleset_indexes(rules, _ACL4SSR_OPENAI_PROVIDER)
    generic_ai_indexes = _ruleset_indexes(rules, _ACL4SSR_AI_PROVIDER)
    if len(lock_indexes) != 1 or len(acl_openai_indexes) != 1 or len(generic_ai_indexes) != 1:
        raise ValidationError("OpenAI App route audit requires one lock/OpenAI/generic-AI rule")
    lock_rule = str(rules[lock_indexes[0]])
    if lock_rule != f"RULE-SET,{RULE_PROVIDER},{OPENAI_SERVICE_TARGET}":
        raise ValidationError("OpenAI App rule does not target service-qualified OpenAI egress")
    if not lock_indexes[0] < acl_openai_indexes[0] < generic_ai_indexes[0]:
        raise ValidationError(
            "OpenAI App route lock must precede ACL4SSR OpenAI and generic AI rules"
        )

    return {"status": "passed", **contract_summary()}


def rewrite_route_locked_candidate(candidate_path: Path) -> dict[str, Any]:
    try:
        original = candidate_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError("failed to read candidate for OpenAI App route lock") from exc
    config = load_yaml_file(candidate_path)
    if not isinstance(config, dict):
        raise ValidationError("candidate is not a YAML mapping")
    report = apply_route_lock(config)
    validate_generated_config(config)
    atomic_write(candidate_path, _comment_header(original) + dump_yaml(config))
    return report
