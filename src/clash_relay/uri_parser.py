"""Parser for common proxy URI subscription entries."""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .errors import SubscriptionError


def _b64decode(value: str) -> bytes:
    cleaned = "".join(value.strip().split()).replace("-", "+").replace("_", "/")
    cleaned += "=" * (-len(cleaned) % 4)
    try:
        return base64.b64decode(cleaned, validate=False)
    except (ValueError, TypeError) as exc:
        raise SubscriptionError("invalid base64 proxy value") from exc


def decode_base64_text(value: str) -> str:
    try:
        return _b64decode(value).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SubscriptionError("base64 subscription is not UTF-8") from exc


def _name(parsed, default: str) -> str:
    fragment = unquote(parsed.fragment) if parsed.fragment else ""
    return fragment.strip() or default


def _port(parsed) -> int:
    try:
        port = parsed.port
    except ValueError as exc:
        raise SubscriptionError("proxy URI contains an invalid port") from exc
    if port is None or not 1 <= port <= 65535:
        raise SubscriptionError("proxy URI is missing a valid port")
    return port


def _bool(query: dict[str, list[str]], key: str, default: bool = False) -> bool:
    value = query.get(key, [str(default)])[0].lower()
    return value in {"1", "true", "yes", "on"}


def _standard_uri(uri: str, proxy_type: str) -> dict[str, Any]:
    parsed = urlsplit(uri)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if not parsed.hostname:
        raise SubscriptionError(f"{proxy_type} URI has no hostname")
    proxy: dict[str, Any] = {
        "name": _name(parsed, f"{proxy_type}-{parsed.hostname}"),
        "type": proxy_type,
        "server": parsed.hostname,
        "port": _port(parsed),
    }
    if proxy_type == "vless":
        if not parsed.username:
            raise SubscriptionError("vless URI has no UUID")
        proxy["uuid"] = unquote(parsed.username)
        proxy["udp"] = True
    elif proxy_type == "trojan":
        if not parsed.username:
            raise SubscriptionError("trojan URI has no password")
        proxy["password"] = unquote(parsed.username)
        proxy["udp"] = True
    elif proxy_type in {"hysteria2", "anytls"}:
        password = parsed.username or parsed.password
        if not password:
            raise SubscriptionError(f"{proxy_type} URI has no password")
        proxy["password"] = unquote(password)
    elif proxy_type == "tuic":
        if not parsed.username or parsed.password is None:
            raise SubscriptionError("tuic URI requires UUID and password")
        proxy["uuid"] = unquote(parsed.username)
        proxy["password"] = unquote(parsed.password)
    elif proxy_type in {"http", "socks5"}:
        if parsed.username:
            proxy["username"] = unquote(parsed.username)
        if parsed.password is not None:
            proxy["password"] = unquote(parsed.password)
        proxy["udp"] = _bool(query, "udp", proxy_type == "socks5")
    network = query.get("type", query.get("network", [""]))[0]
    security = query.get("security", [""])[0]
    if security in {"tls", "reality"} or _bool(query, "tls"):
        proxy["tls"] = True
    servername = query.get("sni", query.get("servername", [""]))[0]
    if servername:
        proxy["servername"] = servername
    if _bool(query, "allowInsecure") or _bool(query, "skip-cert-verify"):
        proxy["skip-cert-verify"] = True
    if network:
        proxy["network"] = network
    if network == "ws":
        headers: dict[str, str] = {}
        host = query.get("host", [""])[0]
        if host:
            headers["Host"] = host
        proxy["ws-opts"] = {
            "path": query.get("path", ["/"])[0] or "/",
            "headers": headers,
        }
    if security == "reality":
        proxy["reality-opts"] = {
            "public-key": query.get("pbk", [""])[0],
            "short-id": query.get("sid", [""])[0],
        }
        proxy["client-fingerprint"] = query.get("fp", ["chrome"])[0]
    flow = query.get("flow", [""])[0]
    if flow:
        proxy["flow"] = flow
    alpn = query.get("alpn", [""])[0]
    if alpn:
        proxy["alpn"] = [item for item in alpn.split(",") if item]
    return proxy


def _parse_ss(uri: str) -> dict[str, Any]:
    body = uri[5:]
    fragment = ""
    if "#" in body:
        body, fragment = body.split("#", 1)
    query_text = ""
    if "?" in body:
        body, query_text = body.split("?", 1)
    if "@" not in body:
        decoded = decode_base64_text(body)
        if "@" not in decoded:
            raise SubscriptionError("ss URI has no server")
        body = decoded
    userinfo, endpoint = body.rsplit("@", 1)
    if ":" not in userinfo:
        userinfo = decode_base64_text(userinfo)
    if ":" not in userinfo:
        raise SubscriptionError("ss URI has no cipher or password")
    cipher, password = userinfo.split(":", 1)
    parsed = urlsplit(f"ss://x@{endpoint}")
    if not parsed.hostname:
        raise SubscriptionError("ss URI has no hostname")
    query = parse_qs(query_text, keep_blank_values=True)
    proxy: dict[str, Any] = {
        "name": unquote(fragment) or f"ss-{parsed.hostname}",
        "type": "ss",
        "server": parsed.hostname,
        "port": _port(parsed),
        "cipher": unquote(cipher),
        "password": unquote(password),
        "udp": True,
    }
    plugin = query.get("plugin", [""])[0]
    if plugin:
        plugin_parts = plugin.split(";", 1)
        proxy["plugin"] = plugin_parts[0]
        if len(plugin_parts) == 2:
            proxy["plugin-opts"] = {
                key: value
                for key, _, value in (item.partition("=") for item in plugin_parts[1].split(";"))
                if key
            }
    return proxy


def _parse_ssr(uri: str) -> dict[str, Any]:
    decoded = decode_base64_text(uri[6:])
    main, _, query_text = decoded.partition("/?")
    parts = main.rsplit(":", 5)
    if len(parts) != 6:
        raise SubscriptionError("ssr URI has invalid fields")
    server, port, protocol, cipher, obfs, password64 = parts
    query = parse_qs(query_text, keep_blank_values=True)
    proxy: dict[str, Any] = {
        "name": "ssr-node",
        "type": "ssr",
        "server": server.strip("[]"),
        "port": int(port),
        "cipher": cipher,
        "password": decode_base64_text(password64),
        "protocol": protocol,
        "obfs": obfs,
        "udp": True,
    }
    if "remarks" in query:
        proxy["name"] = decode_base64_text(query["remarks"][0])
    if "protoparam" in query:
        proxy["protocol-param"] = decode_base64_text(query["protoparam"][0])
    if "obfsparam" in query:
        proxy["obfs-param"] = decode_base64_text(query["obfsparam"][0])
    return proxy


def _parse_vmess(uri: str) -> dict[str, Any]:
    try:
        data = json.loads(decode_base64_text(uri[8:]))
    except (json.JSONDecodeError, TypeError) as exc:
        raise SubscriptionError("vmess URI JSON is invalid") from exc
    if not isinstance(data, dict):
        raise SubscriptionError("vmess URI must decode to an object")
    try:
        port = int(data["port"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SubscriptionError("vmess URI has an invalid port") from exc
    proxy: dict[str, Any] = {
        "name": str(data.get("ps") or f"vmess-{data.get('add', 'node')}"),
        "type": "vmess",
        "server": str(data.get("add") or ""),
        "port": port,
        "uuid": str(data.get("id") or ""),
        "alterId": int(data.get("aid") or 0),
        "cipher": str(data.get("scy") or "auto"),
        "udp": True,
    }
    network = str(data.get("net") or "tcp")
    proxy["network"] = network
    tls = str(data.get("tls") or "").lower()
    if tls:
        proxy["tls"] = True
    servername = str(data.get("sni") or "")
    if servername:
        proxy["servername"] = servername
    if network == "ws":
        headers = {"Host": str(data.get("host"))} if data.get("host") else {}
        proxy["ws-opts"] = {"path": str(data.get("path") or "/"), "headers": headers}
    return proxy


def _parse_hysteria(uri: str) -> dict[str, Any]:
    parsed = urlsplit(uri)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if not parsed.hostname:
        raise SubscriptionError("hysteria URI has no hostname")
    proxy: dict[str, Any] = {
        "name": _name(parsed, f"hysteria-{parsed.hostname}"),
        "type": "hysteria",
        "server": parsed.hostname,
        "port": _port(parsed),
        "auth-str": query.get("auth", query.get("auth-str", [""]))[0],
    }
    for key, target in (("upmbps", "up"), ("downmbps", "down")):
        if key in query:
            proxy[target] = query[key][0]
    if "peer" in query:
        proxy["sni"] = query["peer"][0]
    if _bool(query, "insecure"):
        proxy["skip-cert-verify"] = True
    return proxy


def parse_proxy_uri(uri: str) -> dict[str, Any]:
    value = uri.strip()
    lowered = value.lower()
    if lowered.startswith("ss://"):
        return _parse_ss(value)
    if lowered.startswith("ssr://"):
        return _parse_ssr(value)
    if lowered.startswith("vmess://"):
        return _parse_vmess(value)
    if lowered.startswith("vless://"):
        return _standard_uri(value, "vless")
    if lowered.startswith("trojan://"):
        return _standard_uri(value, "trojan")
    if lowered.startswith(("hysteria2://", "hy2://")):
        return _standard_uri(value.replace("hy2://", "hysteria2://", 1), "hysteria2")
    if lowered.startswith("hysteria://"):
        return _parse_hysteria(value)
    if lowered.startswith("tuic://"):
        return _standard_uri(value, "tuic")
    if lowered.startswith("anytls://"):
        return _standard_uri(value, "anytls")
    if lowered.startswith("socks5://"):
        return _standard_uri(value, "socks5")
    if lowered.startswith("socks://"):
        return _standard_uri("socks5://" + value[8:], "socks5")
    if lowered.startswith("http://"):
        return _standard_uri(value, "http")
    if lowered.startswith("https://"):
        proxy = _standard_uri(value, "http")
        proxy["tls"] = True
        return proxy
    scheme = value.split(":", 1)[0] if ":" in value else "<missing>"
    raise SubscriptionError(f"unsupported proxy URI scheme: {scheme}")
