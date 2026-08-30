"""Parse and sanitize untrusted Clash/Mihomo and URI subscriptions."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any

from .errors import SubscriptionError, UnsafeSubscriptionError
from .uri_parser import decode_base64_text, parse_proxy_uri
from .util import deep_size_guard, stable_json, yaml_load_no_aliases


_ALLOWED_PROXY_TYPES = {
    "ss",
    "ssr",
    "vmess",
    "vless",
    "trojan",
    "http",
    "socks5",
    "snell",
    "hysteria",
    "hysteria2",
    "tuic",
    "anytls",
    "wireguard",
    "ssh",
    "mieru",
    "masque",
}
_FORBIDDEN_FIELDS = {
    "dialer-proxy",
    "interface-name",
    "interface",
    "bind-interface",
    "routing-mark",
    "routing_mark",
    "fwmark",
    "mark",
}
_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "ss": ("cipher", "password"),
    "ssr": ("cipher", "password", "protocol", "obfs"),
    "vmess": ("uuid",),
    "vless": ("uuid",),
    "trojan": ("password",),
    "hysteria": ("auth-str",),
    "hysteria2": ("password",),
    "tuic": ("uuid", "password"),
    "anytls": ("password",),
}
_MAX_PROXIES = 20_000


@dataclass(frozen=True, slots=True)
class ParsedSubscription:
    proxies: tuple[dict[str, Any], ...]
    skipped_items: int


def _private_host(value: str) -> bool:
    lowered = value.lower().strip("[]")
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return False
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _sanitize_mapping(proxy: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for raw_key, value in proxy.items():
        if not isinstance(raw_key, str):
            raise SubscriptionError("proxy fields must use string keys")
        key = raw_key.strip()
        if key in _FORBIDDEN_FIELDS:
            continue
        cleaned[key] = value
    return cleaned


def _validate_proxy(proxy: Any, *, reject_private_hosts: bool) -> dict[str, Any]:
    if not isinstance(proxy, dict):
        raise SubscriptionError("proxy entry must be a mapping")
    deep_size_guard(proxy, max_depth=12, max_items=5000)
    cleaned = _sanitize_mapping(proxy)
    name = cleaned.get("name")
    proxy_type = cleaned.get("type")
    server = cleaned.get("server")
    if not isinstance(name, str) or not name.strip() or len(name) > 256:
        raise SubscriptionError("proxy has no valid name")
    if not isinstance(proxy_type, str):
        raise SubscriptionError(f"proxy {name!r} has no type")
    proxy_type = proxy_type.lower().strip()
    if proxy_type not in _ALLOWED_PROXY_TYPES:
        raise SubscriptionError(f"proxy {name!r} uses unsupported type {proxy_type!r}")
    if not isinstance(server, str) or not server.strip() or len(server) > 253:
        raise SubscriptionError(f"proxy {name!r} has no valid server")
    port = cleaned.get("port")
    if isinstance(port, str) and port.isdigit():
        port = int(port)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise SubscriptionError(f"proxy {name!r} has no valid port")
    if reject_private_hosts and _private_host(server):
        raise SubscriptionError(f"proxy {name!r} targets a private or special-use host")
    for field in _REQUIRED_FIELDS.get(proxy_type, ()):
        if cleaned.get(field) in {None, ""}:
            raise SubscriptionError(f"proxy {name!r} lacks required field {field!r}")
    cleaned["name"] = name.strip()
    cleaned["type"] = proxy_type
    cleaned["server"] = server.strip()
    cleaned["port"] = port
    # Ensure the mapping can be represented deterministically and does not contain exotic objects.
    try:
        stable_json(cleaned)
    except (TypeError, ValueError) as exc:
        raise SubscriptionError(f"proxy {name!r} contains unsupported values") from exc
    return cleaned


def _extract_yaml_proxies(data: Any) -> list[Any] | None:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return None
    proxies: list[Any] = []
    if "proxies" in data:
        if not isinstance(data["proxies"], list):
            raise SubscriptionError("subscription 'proxies' must be a list")
        proxies.extend(data["proxies"])
    providers = data.get("proxy-providers")
    if providers is not None:
        if not isinstance(providers, dict):
            raise SubscriptionError("subscription 'proxy-providers' must be a mapping")
        for provider in providers.values():
            if not isinstance(provider, dict):
                raise SubscriptionError("inline proxy provider must be a mapping")
            if provider.get("type") != "inline":
                # Never follow provider URLs or paths from an untrusted subscription.
                continue
            payload = provider.get("payload")
            if not isinstance(payload, list):
                raise SubscriptionError("inline proxy provider payload must be a list")
            proxies.extend(payload)
    return proxies if proxies or "proxies" in data or providers is not None else None


def _uri_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _parse_payload(text: str, *, depth: int = 0) -> list[Any]:
    if depth > 2:
        raise SubscriptionError("subscription encoding is nested too deeply")
    stripped = text.strip()
    if not stripped:
        return []
    try:
        data = yaml_load_no_aliases(stripped, source="subscription payload", untrusted=True)
    except UnsafeSubscriptionError:
        raise
    except SubscriptionError:
        data = None
    else:
        deep_size_guard(data)
        extracted = _extract_yaml_proxies(data)
        if extracted is not None:
            return extracted
        # PyYAML folds a plain multi-line scalar into a single space-separated
        # string. URI subscriptions are line-oriented, so keep the original
        # source text whenever YAML produced only a scalar.
    lines = _uri_lines(stripped)
    if lines and all("://" in line for line in lines):
        return [parse_proxy_uri(line) for line in lines]
    try:
        decoded = decode_base64_text(stripped)
    except SubscriptionError as exc:
        raise SubscriptionError("payload is neither Clash YAML, proxy URI lines, nor base64") from exc
    if decoded.strip() == stripped:
        raise SubscriptionError("subscription base64 decoding made no progress")
    return _parse_payload(decoded, depth=depth + 1)


def parse_subscription(
    text: str,
    *,
    invalid_policy: str = "error",
    reject_private_hosts: bool = True,
) -> ParsedSubscription:
    if invalid_policy not in {"error", "skip"}:
        raise SubscriptionError(f"unsupported invalid proxy policy: {invalid_policy}")
    entries = _parse_payload(text)
    if len(entries) > _MAX_PROXIES:
        raise SubscriptionError(f"subscription contains more than {_MAX_PROXIES} proxies")
    valid: list[dict[str, Any]] = []
    skipped = 0
    errors: list[str] = []
    for index, entry in enumerate(entries):
        try:
            valid.append(_validate_proxy(entry, reject_private_hosts=reject_private_hosts))
        except (SubscriptionError, ValueError, TypeError) as exc:
            if invalid_policy == "error":
                errors.append(f"item {index + 1}: {exc}")
            else:
                skipped += 1
    if errors:
        rendered = "; ".join(errors[:10])
        extra = "" if len(errors) <= 10 else f"; plus {len(errors) - 10} more"
        raise SubscriptionError(f"subscription contains invalid proxies: {rendered}{extra}")
    return ParsedSubscription(tuple(valid), skipped)
