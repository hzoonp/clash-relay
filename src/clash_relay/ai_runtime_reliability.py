"""Client-path OpenAI runtime reliability for qualified production candidates.

Server-side qualification decides which nodes are eligible for OpenAI. This
module adds a second, client-local layer: each qualified OpenAI region gets its
own inline provider whose health check is executed by the user's Mihomo core,
and the OpenAI service target becomes a stable-first fallback across regions.

The runtime health check deliberately targets the Android ChatGPT endpoint that
motivated this hardening. TLS verification remains enabled; no certificate
bypass is introduced.
"""

from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .openai_app_contract import OPENAI_SERVICE_TARGET
from .util import atomic_write, dump_yaml, load_yaml_file
from .validator import validate_generated_config

OPENAI_ANCHOR_PREFIX = "__CR_AI_OPENAI_"
RUNTIME_PROVIDER_PREFIX = "cr_openai_runtime_"

_RUNTIME_HEALTH_CHECK: dict[str, Any] = {
    "name": "openai_android_client",
    "url": "https://android.chat.openai.com/",
    "interval": 120,
    "timeout": 5000,
    "lazy": False,
    "expected-status": "200-499",
    "max-failed-times": 2,
}


def runtime_health_contract() -> dict[str, Any]:
    """Return the public, non-sensitive OpenAI client health-check contract."""
    return dict(_RUNTIME_HEALTH_CHECK)


def _comment_header(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def _runtime_provider_name(anchor_name: str) -> str:
    digest = hashlib.sha256(anchor_name.encode("utf-8")).hexdigest()[:12]
    return f"{RUNTIME_PROVIDER_PREFIX}{digest}"


def _runtime_proxy_name(anchor_name: str, original_name: str) -> str:
    digest = hashlib.sha256(f"{anchor_name}\0{original_name}".encode()).hexdigest()[:8]
    return f"{original_name} [OAI:{digest}]"


def _provider_health_check() -> dict[str, Any]:
    contract = runtime_health_contract()
    return {
        "enable": True,
        "url": contract["url"],
        "interval": contract["interval"],
        "timeout": contract["timeout"],
        "lazy": contract["lazy"],
        "expected-status": contract["expected-status"],
    }


def _target_test_fields() -> dict[str, Any]:
    contract = runtime_health_contract()
    return {
        "url": contract["url"],
        "interval": contract["interval"],
        "timeout": contract["timeout"],
        "lazy": contract["lazy"],
        "expected-status": contract["expected-status"],
        "max-failed-times": contract["max-failed-times"],
    }


def _groups_by_name(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = config.get("proxy-groups")
    if not isinstance(rows, list):
        raise ValidationError("OpenAI client-path hardening requires proxy-groups")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise ValidationError("OpenAI client-path hardening found a malformed proxy group")
        name = str(row["name"])
        if name in result:
            raise ValidationError("OpenAI client-path hardening found duplicate proxy groups")
        result[name] = row
    return result


def _selected_payload(provider: dict[str, Any], filter_pattern: str) -> list[dict[str, Any]]:
    payload = provider.get("payload")
    if not isinstance(payload, list):
        raise ValidationError("OpenAI client-path source provider has no inline payload")
    try:
        matcher = re.compile(filter_pattern)
    except re.error as exc:
        raise ValidationError("OpenAI client-path anchor filter is invalid") from exc
    selected: list[dict[str, Any]] = []
    for proxy in payload:
        if not isinstance(proxy, dict) or not isinstance(proxy.get("name"), str):
            raise ValidationError("OpenAI client-path source provider contains an unnamed proxy")
        if matcher.fullmatch(str(proxy["name"])):
            selected.append(proxy)
    if not selected:
        raise ValidationError("OpenAI client-path anchor resolved to no qualified nodes")
    return selected


def _clone_runtime_provider(
    providers: dict[str, Any],
    *,
    anchor_name: str,
    source_provider: dict[str, Any],
    filter_pattern: str,
) -> tuple[str, int]:
    runtime_provider_name = _runtime_provider_name(anchor_name)
    if runtime_provider_name in providers:
        raise ValidationError("OpenAI client-path runtime provider name collision")

    payload: list[dict[str, Any]] = []
    for proxy in _selected_payload(source_provider, filter_pattern):
        clone = copy.deepcopy(proxy)
        clone["name"] = _runtime_proxy_name(anchor_name, str(proxy["name"]))
        payload.append(clone)

    provider = {
        key: copy.deepcopy(value)
        for key, value in source_provider.items()
        if key not in {"health-check", "payload"}
    }
    provider["type"] = "inline"
    provider["health-check"] = _provider_health_check()
    provider["payload"] = payload
    providers[runtime_provider_name] = provider
    return runtime_provider_name, len(payload)


def _is_hardened(config: dict[str, Any]) -> bool:
    try:
        groups = _groups_by_name(config)
    except ValidationError:
        return False
    target = groups.get(OPENAI_SERVICE_TARGET)
    if not isinstance(target, dict):
        return False
    references = target.get("proxies")
    if references == ["REJECT"]:
        return True
    if target.get("type") != "fallback" or not isinstance(references, list) or not references:
        return False
    for reference in references:
        anchor = groups.get(str(reference))
        if not isinstance(anchor, dict) or anchor.get("type") != "fallback":
            return False
        uses = anchor.get("use")
        if (
            not isinstance(uses, list)
            or len(uses) != 1
            or not str(uses[0]).startswith(RUNTIME_PROVIDER_PREFIX)
        ):
            return False
    return True


def apply_openai_client_path_hardening(config: dict[str, Any]) -> dict[str, Any]:
    """Add client-local OpenAI health checks and stable-first failover."""
    if _is_hardened(config):
        return audit_openai_client_path(config)

    providers = config.get("proxy-providers")
    if not isinstance(providers, dict):
        raise ValidationError("OpenAI client-path hardening requires proxy-providers")
    groups = _groups_by_name(config)
    target = groups.get(OPENAI_SERVICE_TARGET)
    if not isinstance(target, dict):
        raise ValidationError("OpenAI client-path hardening requires the OpenAI service target")
    references = target.get("proxies")
    if references == ["REJECT"]:
        return {
            "status": "fail_closed",
            "runtime_regions": 0,
            "runtime_providers": 0,
            "runtime_nodes": 0,
            "selection": "reject",
            "health_check": runtime_health_contract(),
        }
    if not isinstance(references, list) or not references:
        raise ValidationError("OpenAI client-path service target has no runtime regions")

    runtime_nodes = 0
    runtime_providers = 0
    for reference in references:
        anchor_name = str(reference)
        if not anchor_name.startswith(OPENAI_ANCHOR_PREFIX):
            raise ValidationError("OpenAI client-path target references a non-OpenAI anchor")
        anchor = groups.get(anchor_name)
        if not isinstance(anchor, dict) or anchor.get("hidden") is not True:
            raise ValidationError(
                "OpenAI client-path target references a missing/non-hidden anchor"
            )
        uses = anchor.get("use")
        filter_pattern = anchor.get("filter")
        if not isinstance(uses, list) or len(uses) != 1 or not isinstance(filter_pattern, str):
            raise ValidationError("OpenAI client-path anchor is not a qualified provider filter")
        source_provider = providers.get(str(uses[0]))
        if not isinstance(source_provider, dict):
            raise ValidationError("OpenAI client-path anchor references a missing provider")

        runtime_provider_name, count = _clone_runtime_provider(
            providers,
            anchor_name=anchor_name,
            source_provider=source_provider,
            filter_pattern=filter_pattern,
        )
        runtime_nodes += count
        runtime_providers += 1
        anchor.clear()
        anchor.update(
            {
                "name": anchor_name,
                "type": "fallback",
                "hidden": True,
                "use": [runtime_provider_name],
            }
        )

    target.clear()
    target.update(
        {
            "name": OPENAI_SERVICE_TARGET,
            "type": "fallback",
            "hidden": True,
            "proxies": [str(reference) for reference in references],
            **_target_test_fields(),
        }
    )

    validate_generated_config(config)
    report = audit_openai_client_path(config)
    report.update(
        {
            "status": "hardened",
            "runtime_nodes": runtime_nodes,
            "runtime_providers": runtime_providers,
        }
    )
    return report


def audit_openai_client_path(config: dict[str, Any]) -> dict[str, Any]:
    """Fail closed if the post-qualification OpenAI path loses local health checks."""
    providers = config.get("proxy-providers")
    if not isinstance(providers, dict):
        raise ValidationError("OpenAI client-path audit requires proxy-providers")
    groups = _groups_by_name(config)
    target = groups.get(OPENAI_SERVICE_TARGET)
    if target is None:
        return {
            "status": "pre_qualification",
            "runtime_regions": 0,
            "runtime_providers": 0,
            "runtime_nodes": 0,
            "selection": "not_materialized",
            "health_check": runtime_health_contract(),
        }
    references = target.get("proxies")
    if references == ["REJECT"]:
        return {
            "status": "fail_closed",
            "runtime_regions": 0,
            "runtime_providers": 0,
            "runtime_nodes": 0,
            "selection": "reject",
            "health_check": runtime_health_contract(),
        }
    if target.get("type") != "fallback" or not isinstance(references, list) or not references:
        raise ValidationError("OpenAI service target must be a client-path fallback")

    expected_target_fields = _target_test_fields()
    for key, value in expected_target_fields.items():
        if target.get(key) != value:
            raise ValidationError("OpenAI client-path target health-check contract drifted")

    runtime_nodes = 0
    runtime_provider_names: set[str] = set()
    expected_provider_health = _provider_health_check()
    for reference in references:
        anchor_name = str(reference)
        if not anchor_name.startswith(OPENAI_ANCHOR_PREFIX):
            raise ValidationError("OpenAI client-path target references a non-OpenAI anchor")
        anchor = groups.get(anchor_name)
        if (
            not isinstance(anchor, dict)
            or anchor.get("hidden") is not True
            or anchor.get("type") != "fallback"
        ):
            raise ValidationError("OpenAI client-path anchor must be a hidden fallback")
        uses = anchor.get("use")
        if not isinstance(uses, list) or len(uses) != 1:
            raise ValidationError("OpenAI client-path anchor must use one runtime provider")
        provider_name = str(uses[0])
        if not provider_name.startswith(RUNTIME_PROVIDER_PREFIX):
            raise ValidationError("OpenAI client-path anchor bypasses its runtime provider")
        provider = providers.get(provider_name)
        if not isinstance(provider, dict) or provider.get("type") != "inline":
            raise ValidationError("OpenAI client-path runtime provider is missing or not inline")
        if provider.get("health-check") != expected_provider_health:
            raise ValidationError("OpenAI client-path provider health-check contract drifted")
        payload = provider.get("payload")
        if not isinstance(payload, list) or not payload:
            raise ValidationError("OpenAI client-path runtime provider is empty")
        for proxy in payload:
            if not isinstance(proxy, dict) or not isinstance(proxy.get("name"), str):
                raise ValidationError(
                    "OpenAI client-path runtime provider contains an unnamed proxy"
                )
            if " [OAI:" not in str(proxy["name"]):
                raise ValidationError("OpenAI client-path runtime proxy name is not isolated")
        runtime_nodes += len(payload)
        runtime_provider_names.add(provider_name)

    return {
        "status": "passed",
        "runtime_regions": len(references),
        "runtime_providers": len(runtime_provider_names),
        "runtime_nodes": runtime_nodes,
        "selection": "stable_first_fallback",
        "health_check": runtime_health_contract(),
    }


def rewrite_openai_client_path_candidate(candidate_path: Path) -> dict[str, Any]:
    try:
        original = candidate_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError("failed to read candidate for OpenAI client-path hardening") from exc
    config = load_yaml_file(candidate_path)
    if not isinstance(config, dict):
        raise ValidationError("candidate is not a YAML mapping")
    report = apply_openai_client_path_hardening(config)
    validate_generated_config(config)
    atomic_write(candidate_path, _comment_header(original) + dump_yaml(config))
    return report
