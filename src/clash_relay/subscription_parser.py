"""Parse and sanitize untrusted Clash/Mihomo and URI subscriptions."""

from __future__ import annotations

import ipaddress
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .errors import SubscriptionError, UnsafeSubscriptionError
from .network_address_policy import is_global_address
from .uri_parser import decode_base64_text, parse_proxy_uri
from .util import (
    contains_yaml_incompatible_control_characters,
    deep_size_guard,
    stable_json,
    yaml_load_no_aliases,
)

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
_URI_LINE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://\S+$")
_SUPPORTED_URI_PREFIXES = (
    "ss://",
    "ssr://",
    "vmess://",
    "vless://",
    "trojan://",
    "hysteria://",
    "hysteria2://",
    "hy2://",
    "tuic://",
    "anytls://",
    "socks://",
    "socks5://",
    "http://",
    "https://",
)


@dataclass(frozen=True, slots=True)
class ParsedSubscription:
    proxies: tuple[dict[str, Any], ...]
    skipped_items: int
    skipped_reason_counts: tuple[tuple[str, int], ...] = ()
    empty_payload_shape: str | None = None


def _private_host(value: str) -> bool:
    lowered = value.lower().rstrip(".")
    if lowered.startswith("[") and lowered.endswith("]"):
        lowered = lowered[1:-1]
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return False
    return not is_global_address(address)


def _sanitize_mapping(proxy: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for raw_key, value in proxy.items():
        if not isinstance(raw_key, str):
            raise SubscriptionError("proxy fields must use string keys")
        key = raw_key.strip()
        if key in _FORBIDDEN_FIELDS:
            continue
        # Mihomo treats an explicitly null structured option block as invalid.
        # Semantically, null means the subscription supplied no options, so
        # normalize it to an absent field before any inventory sees the node.
        if key.endswith("-opts") and value is None:
            continue
        cleaned[key] = value
    return cleaned


def _validate_structured_options(proxy: dict[str, Any], *, name: str) -> None:
    """Reject malformed Mihomo option containers before they reach any inventory.

    Clash/Mihomo option fields such as ws-opts, grpc-opts, h2-opts,
    reality-opts, and plugin-opts are mappings. Accepting a scalar or list here
    lets an otherwise plausible subscription node survive ingestion and later
    makes Mihomo reject every generated provider that contains it.
    """

    for key, value in proxy.items():
        if not key.endswith("-opts"):
            continue
        if not isinstance(value, dict):
            raise SubscriptionError(f"proxy {name!r} has malformed structured option field {key!r}")


def _validate_proxy(proxy: Any, *, reject_private_hosts: bool) -> dict[str, Any]:
    if not isinstance(proxy, dict):
        raise SubscriptionError("proxy entry must be a mapping")
    deep_size_guard(proxy, max_depth=12, max_items=5000)
    cleaned = _sanitize_mapping(proxy)
    if contains_yaml_incompatible_control_characters(cleaned):
        raise SubscriptionError("proxy contains YAML-incompatible control characters")
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
    if not isinstance(server, str):
        raise SubscriptionError(f"proxy {name!r} has no valid server")
    server = server.strip()
    if not server or len(server) > 253:
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
    _validate_structured_options(cleaned, name=name)
    cleaned["name"] = name.strip()
    cleaned["type"] = proxy_type
    cleaned["server"] = server
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


def _empty_yaml_payload_shape(data: Any) -> str:
    if isinstance(data, list):
        return "yaml_empty_list"
    if not isinstance(data, dict):
        return "yaml_empty_inventory"

    proxies = data.get("proxies")
    providers = data.get("proxy-providers")
    if (
        isinstance(providers, dict)
        and providers
        and (proxies is None or proxies == [])
        and all(
            isinstance(provider, dict) and provider.get("type") != "inline"
            for provider in providers.values()
        )
    ):
        return "remote_provider_only"
    if "proxies" in data and proxies == [] and providers is None:
        return "yaml_empty_proxies"
    return "yaml_empty_inventory"


def _parse_payload(
    text: str,
    *,
    invalid_policy: str,
    depth: int = 0,
) -> tuple[list[Any], str | None, int, Counter[str]]:
    if depth > 2:
        raise SubscriptionError("subscription encoding is nested too deeply")
    stripped = text.strip()
    if not stripped:
        return [], "empty_text", 0, Counter()
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
            if not extracted:
                return extracted, _empty_yaml_payload_shape(data), 0, Counter()
            return extracted, None, 0, Counter()
        # PyYAML folds a plain multi-line scalar into a single space-separated
        # string. URI subscriptions are line-oriented, so keep the original
        # source text whenever YAML produced only a scalar.
    lines = _uri_lines(stripped)
    # A supported URI at the start of a whole line identifies a list. Once
    # identified, every non-comment line is processed independently. URI-like
    # lists of unknown schemes are also processed so skip policy can report
    # them without misclassifying HTML pages containing an embedded URL.
    uri_candidate = any(line.lower().startswith(_SUPPORTED_URI_PREFIXES) for line in lines) or (
        bool(lines) and all(_URI_LINE.fullmatch(line) for line in lines)
    )
    if uri_candidate:
        if len(lines) > _MAX_PROXIES:
            raise SubscriptionError(f"subscription contains more than {_MAX_PROXIES} proxies")
        entries: list[Any] = []
        skipped_reasons: Counter[str] = Counter()
        for line in lines:
            if not _URI_LINE.fullmatch(line):
                if invalid_policy == "error":
                    raise SubscriptionError("subscription contains garbage URI line")
                skipped_reasons["garbage_uri_line"] += 1
                continue
            try:
                entries.append(parse_proxy_uri(line))
            except (SubscriptionError, ValueError, TypeError) as exc:
                if invalid_policy == "error":
                    raise SubscriptionError(
                        f"subscription contains invalid proxy URI: {_invalid_proxy_reason(exc)}"
                    ) from exc
                skipped_reasons[_invalid_proxy_reason(exc)] += 1
        return entries, None, sum(skipped_reasons.values()), skipped_reasons
    try:
        decoded = decode_base64_text(stripped)
    except SubscriptionError as exc:
        raise SubscriptionError(
            "payload is neither Clash YAML, proxy URI lines, nor base64"
        ) from exc
    if decoded.strip() == stripped:
        raise SubscriptionError("subscription base64 decoding made no progress")
    return _parse_payload(decoded, invalid_policy=invalid_policy, depth=depth + 1)


def _invalid_proxy_reason(error: BaseException) -> str:
    message = str(error)
    if message.startswith("unsupported proxy URI scheme"):
        return "unknown_uri_scheme"
    if any(
        marker in message
        for marker in (
            "unsupported URI parameter",
            "unsupported TLS setting",
            "unsupported transport",
            "unsupported path",
        )
    ):
        return "unsupported_uri_parameter"
    if "invalid port" in message or "valid port" in message:
        return "invalid_port"
    if "no password" in message or "no UUID" in message or "requires UUID and password" in message:
        return "invalid_auth"
    if "URI" in message or "base64" in message:
        return "malformed_uri"
    if message == "proxy entry must be a mapping":
        return "invalid_entry"
    if message == "proxy fields must use string keys":
        return "invalid_fields"
    if message == "proxy contains YAML-incompatible control characters":
        return "yaml_control_characters"
    if message.endswith("has no valid name"):
        return "invalid_name"
    if message.endswith("has no type"):
        return "missing_type"
    if " uses unsupported type " in message:
        return "unsupported_type"
    if message.endswith("has no valid server"):
        return "invalid_server"
    if message.endswith("has no valid port"):
        return "invalid_port"
    if message.endswith("targets a private or special-use host"):
        return "private_host"
    if " lacks required field " in message:
        return "missing_required_field"
    if " has malformed structured option field " in message:
        return "malformed_options"
    if message.endswith("contains unsupported values"):
        return "unsupported_values"
    return "other_invalid"


def parse_subscription(
    text: str,
    *,
    invalid_policy: str = "error",
    reject_private_hosts: bool = True,
) -> ParsedSubscription:
    if invalid_policy not in {"error", "skip"}:
        raise SubscriptionError(f"unsupported invalid proxy policy: {invalid_policy}")
    entries, empty_payload_shape, skipped, skipped_reasons = _parse_payload(
        text,
        invalid_policy=invalid_policy,
    )
    if len(entries) > _MAX_PROXIES:
        raise SubscriptionError(f"subscription contains more than {_MAX_PROXIES} proxies")
    valid: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, entry in enumerate(entries):
        try:
            valid.append(_validate_proxy(entry, reject_private_hosts=reject_private_hosts))
        except (SubscriptionError, ValueError, TypeError) as exc:
            if invalid_policy == "error":
                errors.append(f"item {index + 1}: {exc}")
            else:
                skipped += 1
                skipped_reasons[_invalid_proxy_reason(exc)] += 1
    if errors:
        rendered = "; ".join(errors[:10])
        extra = "" if len(errors) <= 10 else f"; plus {len(errors) - 10} more"
        raise SubscriptionError(f"subscription contains invalid proxies: {rendered}{extra}")
    return ParsedSubscription(
        tuple(valid),
        skipped,
        tuple(sorted(skipped_reasons.items())),
        empty_payload_shape=empty_payload_shape if not entries else None,
    )
