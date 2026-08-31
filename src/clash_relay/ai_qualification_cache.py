"""Private incremental cache for service-specific AI qualification results."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from .ai_qualification import AI_PROVIDER_PREFIX
from .errors import ValidationError

_STATE_VERSION = 1
_DOMAIN = b"clash-relay/ai-qualification-cache/v1"
_MAX_RECORDS = 4096
_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
_DEFAULT_PASS_TTL_SECONDS = 6 * 60 * 60
_DEFAULT_FAILURE_TTL_SECONDS = 60 * 60


def derive_ai_cache_key(token: str) -> bytes:
    if not token:
        raise ValidationError("AI qualification cache requires a non-empty private key source")
    return hmac.new(token.encode("utf-8"), _DOMAIN, hashlib.sha256).digest()


def empty_ai_cache() -> dict[str, Any]:
    return {"version": _STATE_VERSION, "nodes": {}}


def parse_ai_cache_bytes(content: bytes | None) -> tuple[dict[str, Any], str]:
    if not content:
        return empty_ai_cache(), "missing"
    try:
        document = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return empty_ai_cache(), "invalid"
    if not isinstance(document, dict) or document.get("version") != _STATE_VERSION:
        return empty_ai_cache(), "invalid"
    nodes = document.get("nodes")
    if not isinstance(nodes, dict):
        return empty_ai_cache(), "invalid"
    clean: dict[str, dict[str, Any]] = {}
    for fingerprint, record in nodes.items():
        if (
            not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or not isinstance(record, dict)
        ):
            return empty_ai_cache(), "invalid"
        services = record.get("services")
        if not isinstance(services, dict):
            return empty_ai_cache(), "invalid"
        clean_services: dict[str, dict[str, Any]] = {}
        for service, result in services.items():
            if not isinstance(service, str) or not isinstance(result, dict):
                return empty_ai_cache(), "invalid"
            passed = result.get("passed")
            checked = result.get("checked_epoch")
            if not isinstance(passed, bool) or not isinstance(checked, int) or checked < 0:
                return empty_ai_cache(), "invalid"
            clean_services[service] = {"passed": passed, "checked_epoch": checked}
        clean[fingerprint] = {"services": clean_services}
    return {"version": _STATE_VERSION, "nodes": clean}, "loaded"


def _proxy_fingerprint(provider_name: str, proxy: dict[str, Any], key: bytes) -> str:
    canonical = json.dumps(
        {"provider": provider_name, "proxy": proxy},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(key, canonical, hashlib.sha256).hexdigest()


def ai_runtime_fingerprints(candidate: dict[str, Any], key: bytes) -> dict[str, str]:
    providers = candidate.get("proxy-providers")
    if not isinstance(providers, dict):
        raise ValidationError("AI qualification cache requires candidate proxy-providers")
    result: dict[str, str] = {}
    for provider_name in sorted(providers):
        if not str(provider_name).startswith(AI_PROVIDER_PREFIX):
            continue
        provider = providers[provider_name]
        payload = provider.get("payload") if isinstance(provider, dict) else None
        if not isinstance(payload, list):
            raise ValidationError("AI qualification cache found an invalid AI provider")
        for proxy in payload:
            if not isinstance(proxy, dict) or not isinstance(proxy.get("name"), str):
                raise ValidationError("AI qualification cache found an unnamed AI proxy")
            runtime_name = str(proxy["name"])
            fingerprint = _proxy_fingerprint(str(provider_name), proxy, key)
            previous = result.get(runtime_name)
            if previous is not None and previous != fingerprint:
                raise ValidationError("AI qualification cache found a duplicate runtime proxy name")
            result[runtime_name] = fingerprint
    if not result:
        raise ValidationError("AI qualification cache found no candidate AI nodes")
    return result


def cached_service_decisions(
    cache: dict[str, Any],
    fingerprints: dict[str, str],
    service: str,
    *,
    now_epoch: int | None = None,
    pass_ttl_seconds: int = _DEFAULT_PASS_TTL_SECONDS,
    failure_ttl_seconds: int = _DEFAULT_FAILURE_TTL_SECONDS,
) -> tuple[set[str], set[str], set[str]]:
    """Return cached passes, cached failures, and names requiring a live probe."""
    now = int(time.time()) if now_epoch is None else int(now_epoch)
    nodes = cache.get("nodes", {})
    if not isinstance(nodes, dict):
        nodes = {}
    passed: set[str] = set()
    failed: set[str] = set()
    live: set[str] = set()
    for runtime_name, fingerprint in fingerprints.items():
        record = nodes.get(fingerprint)
        services = record.get("services") if isinstance(record, dict) else None
        result = services.get(service) if isinstance(services, dict) else None
        if not isinstance(result, dict):
            live.add(runtime_name)
            continue
        checked = result.get("checked_epoch")
        cached_passed = result.get("passed")
        if not isinstance(checked, int) or not isinstance(cached_passed, bool):
            live.add(runtime_name)
            continue
        age = now - checked
        ttl = pass_ttl_seconds if cached_passed else failure_ttl_seconds
        if age < 0 or age > ttl:
            live.add(runtime_name)
        elif cached_passed:
            passed.add(runtime_name)
        else:
            failed.add(runtime_name)
    return passed, failed, live


def update_ai_cache_service(
    cache: dict[str, Any],
    fingerprints: dict[str, str],
    service: str,
    *,
    checked_names: set[str],
    passed_names: set[str],
    now_epoch: int | None = None,
) -> dict[str, Any]:
    now = int(time.time()) if now_epoch is None else int(now_epoch)
    old_nodes = cache.get("nodes", {})
    nodes: dict[str, dict[str, Any]] = {}
    if isinstance(old_nodes, dict):
        for fingerprint, record in old_nodes.items():
            if not isinstance(record, dict):
                continue
            services = record.get("services")
            if not isinstance(services, dict):
                continue
            newest = max(
                (
                    int(item.get("checked_epoch", 0))
                    for item in services.values()
                    if isinstance(item, dict)
                ),
                default=0,
            )
            if newest > 0 and 0 <= now - newest <= _MAX_AGE_SECONDS:
                nodes[str(fingerprint)] = {
                    "services": {
                        str(name): {
                            "passed": bool(item["passed"]),
                            "checked_epoch": int(item["checked_epoch"]),
                        }
                        for name, item in services.items()
                        if isinstance(item, dict)
                        and isinstance(item.get("passed"), bool)
                        and isinstance(item.get("checked_epoch"), int)
                    }
                }

    for runtime_name in sorted(checked_names):
        fingerprint = fingerprints[runtime_name]
        record = nodes.setdefault(fingerprint, {"services": {}})
        services = record.setdefault("services", {})
        services[service] = {
            "passed": runtime_name in passed_names,
            "checked_epoch": now,
        }

    if len(nodes) > _MAX_RECORDS:
        ranked = sorted(
            nodes.items(),
            key=lambda item: max(
                (
                    int(result.get("checked_epoch", 0))
                    for result in item[1].get("services", {}).values()
                    if isinstance(result, dict)
                ),
                default=0,
            ),
            reverse=True,
        )[:_MAX_RECORDS]
        nodes = dict(ranked)
    return {"version": _STATE_VERSION, "nodes": dict(sorted(nodes.items()))}


def ai_cache_summary(cache: dict[str, Any]) -> dict[str, int]:
    nodes = cache.get("nodes", {})
    service_records = 0
    if isinstance(nodes, dict):
        for record in nodes.values():
            services = record.get("services") if isinstance(record, dict) else None
            if isinstance(services, dict):
                service_records += len(services)
    return {
        "records": len(nodes) if isinstance(nodes, dict) else 0,
        "service_records": service_records,
    }
