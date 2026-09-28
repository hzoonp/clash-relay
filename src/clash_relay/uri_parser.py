"""Parser for common proxy URI subscription entries."""

from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .errors import SubscriptionError


def _b64decode(value: str) -> bytes:
    cleaned = "".join(value.strip().split()).replace("-", "+").replace("_", "/")
    cleaned += "=" * (-len(cleaned) % 4)
    try:
        return base64.b64decode(cleaned, validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
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


def _userinfo(parsed, *, protocol: str) -> str:
    # urlsplit.username deliberately excludes everything after the first colon.
    # Hysteria2's complete userinfo is its authentication string.
    authority = parsed.netloc.rsplit("@", 1)
    if len(authority) != 2 or not authority[0]:
        raise SubscriptionError(f"{protocol} URI has no password")
    return unquote(authority[0])


def _single_query(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key, [])
    if len(values) > 1:
        raise SubscriptionError("proxy URI has duplicate parameters")
    return values[0] if values else ""


def _strict_bool(query: dict[str, list[str]], key: str) -> bool:
    value = _single_query(query, key).lower()
    if value not in {"", "0", "false", "no", "off", "1", "true", "yes", "on"}:
        raise SubscriptionError("proxy URI has malformed boolean parameter")
    return value in {"1", "true", "yes", "on"}


def _parse_hysteria2(uri: str) -> dict[str, Any]:
    parsed = urlsplit(uri)
    if not parsed.hostname:
        raise SubscriptionError("hysteria2 URI has no hostname")
    if parsed.path not in {"", "/"}:
        raise SubscriptionError("hysteria2 URI has an unsupported path")
    query = parse_qs(parsed.query, keep_blank_values=True)
    allowed = {"sni", "servername", "obfs", "obfs-password", "insecure", "upmbps", "downmbps"}
    if set(query) - allowed:
        raise SubscriptionError("hysteria2 URI has unsupported URI parameter")
    # Mihomo's parser accepts one integer port. A port-hopping expression is
    # therefore rejected instead of silently changing the destination.
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise SubscriptionError("proxy URI contains an invalid port") from exc
    port = _port(parsed) if parsed_port is not None else 443
    proxy: dict[str, Any] = {
        "name": _name(parsed, f"hysteria2-{parsed.hostname}"),
        "type": "hysteria2",
        "server": parsed.hostname,
        "port": port,
        "password": _userinfo(parsed, protocol="hysteria2"),
    }
    if "sni" in query and "servername" in query:
        raise SubscriptionError("proxy URI has duplicate parameters")
    sni = _single_query(query, "sni") or _single_query(query, "servername")
    if sni:
        proxy["sni"] = sni
    obfs = _single_query(query, "obfs")
    obfs_password = _single_query(query, "obfs-password")
    if obfs:
        if obfs != "salamander" or not obfs_password:
            raise SubscriptionError("hysteria2 URI has unsupported URI parameter")
        proxy["obfs"] = obfs
        proxy["obfs-password"] = obfs_password
    elif obfs_password:
        raise SubscriptionError("hysteria2 URI has unsupported URI parameter")
    for source, target in (("upmbps", "up"), ("downmbps", "down")):
        value = _single_query(query, source)
        if value:
            if not re.fullmatch(r"[1-9][0-9]*", value):
                raise SubscriptionError("hysteria2 URI has unsupported URI parameter")
            proxy[target] = int(value)
    if _strict_bool(query, "insecure"):
        raise SubscriptionError("hysteria2 URI has unsupported TLS setting")
    return proxy


def _bool(query: dict[str, list[str]], key: str, default: bool = False) -> bool:
    value = query.get(key, [str(default)])[0].lower()
    return value in {"1", "true", "yes", "on"}


def _standard_base(uri: str, proxy_type: str) -> tuple[Any, dict[str, list[str]], dict[str, Any]]:
    parsed = urlsplit(uri)
    if parsed.path not in {"", "/"}:
        raise SubscriptionError(f"{proxy_type} URI has an unsupported path")
    query = parse_qs(parsed.query, keep_blank_values=True)
    common_parameters = {
        "type",
        "network",
        "security",
        "tls",
        "sni",
        "servername",
        "allowInsecure",
        "skip-cert-verify",
        "host",
        "path",
        "serviceName",
        "service-name",
        "pbk",
        "sid",
        "fp",
        "flow",
        "alpn",
        "udp",
    }
    if set(query) - common_parameters:
        raise SubscriptionError(f"{proxy_type} URI has unsupported URI parameter")
    if any(len(values) != 1 for values in query.values()):
        raise SubscriptionError("proxy URI has duplicate parameters")
    if "sni" in query and "servername" in query:
        raise SubscriptionError("proxy URI has duplicate parameters")
    if "serviceName" in query and "service-name" in query:
        raise SubscriptionError("proxy URI has duplicate parameters")
    security = query.get("security", [""])[0]
    network = query.get("type", query.get("network", [""]))[0]
    if security not in {"", "none", "tls", "reality"} or (
        security == "reality" and proxy_type != "vless"
    ):
        raise SubscriptionError(f"{proxy_type} URI has unsupported TLS setting")
    if network not in {"", "tcp", "ws", "grpc"}:
        raise SubscriptionError(f"{proxy_type} URI has unsupported transport")
    if network in {"ws", "grpc"} and proxy_type not in {"vless", "trojan"}:
        raise SubscriptionError(f"{proxy_type} URI has unsupported transport")
    if any(key in query for key in ("pbk", "sid", "fp")) and security != "reality":
        raise SubscriptionError(f"{proxy_type} URI has unsupported TLS setting")
    if "flow" in query and proxy_type != "vless":
        raise SubscriptionError(f"{proxy_type} URI has unsupported transport")
    if any(key in query for key in ("host", "path")) and network != "ws":
        raise SubscriptionError(f"{proxy_type} URI has unsupported transport")
    if any(key in query for key in ("serviceName", "service-name")) and network != "grpc":
        raise SubscriptionError(f"{proxy_type} URI has unsupported transport")
    if "type" in query and "network" in query and query["type"] != query["network"]:
        raise SubscriptionError(f"{proxy_type} URI has unsupported transport")
    for key in ("tls", "allowInsecure", "skip-cert-verify", "udp"):
        _strict_bool(query, key)
    if not parsed.hostname:
        raise SubscriptionError(f"{proxy_type} URI has no hostname")
    proxy: dict[str, Any] = {
        "name": _name(parsed, f"{proxy_type}-{parsed.hostname}"),
        "type": proxy_type,
        "server": parsed.hostname,
        "port": _port(parsed),
    }
    return parsed, query, proxy


def _apply_standard_options(proxy: dict[str, Any], query: dict[str, list[str]]) -> None:
    proxy_type = str(proxy["type"])
    network = query.get("type", query.get("network", [""]))[0]
    security = query.get("security", [""])[0]
    if "udp" in query:
        proxy["udp"] = _strict_bool(query, "udp")
    if security in {"tls", "reality"} or _bool(query, "tls"):
        proxy["tls"] = True
    servername = query.get("sni", query.get("servername", [""]))[0]
    if servername:
        sni_field = (
            "sni" if proxy_type in {"trojan", "hysteria2", "tuic", "anytls"} else "servername"
        )
        proxy[sni_field] = servername
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
    if network == "grpc":
        service_name = query.get("serviceName", query.get("service-name", [""]))[0]
        if service_name:
            proxy["grpc-opts"] = {"grpc-service-name": service_name}
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


def _parse_vless(uri: str) -> dict[str, Any]:
    parsed, query, proxy = _standard_base(uri, "vless")
    if not parsed.username:
        raise SubscriptionError("vless URI has no UUID")
    proxy["uuid"] = unquote(parsed.username)
    proxy["udp"] = True
    _apply_standard_options(proxy, query)
    return proxy


def _parse_trojan(uri: str) -> dict[str, Any]:
    parsed, query, proxy = _standard_base(uri, "trojan")
    if not parsed.username:
        raise SubscriptionError("trojan URI has no password")
    proxy["password"] = unquote(parsed.username)
    proxy["udp"] = True
    _apply_standard_options(proxy, query)
    return proxy


def _parse_tuic(uri: str) -> dict[str, Any]:
    parsed, query, proxy = _standard_base(uri, "tuic")
    if not parsed.username or parsed.password is None:
        raise SubscriptionError("tuic URI requires UUID and password")
    proxy["uuid"] = unquote(parsed.username)
    proxy["password"] = unquote(parsed.password)
    _apply_standard_options(proxy, query)
    return proxy


def _parse_anytls(uri: str) -> dict[str, Any]:
    parsed, query, proxy = _standard_base(uri, "anytls")
    password = parsed.username or parsed.password
    if not password:
        raise SubscriptionError("anytls URI has no password")
    proxy["password"] = unquote(password)
    _apply_standard_options(proxy, query)
    return proxy


def _parse_http(uri: str, *, tls: bool = False) -> dict[str, Any]:
    parsed, query, proxy = _standard_base(uri, "http")
    if parsed.username:
        proxy["username"] = unquote(parsed.username)
    if parsed.password is not None:
        proxy["password"] = unquote(parsed.password)
    proxy["udp"] = False
    _apply_standard_options(proxy, query)
    if tls:
        proxy["tls"] = True
    return proxy


def _parse_socks5(uri: str) -> dict[str, Any]:
    parsed, query, proxy = _standard_base(uri, "socks5")
    if parsed.username:
        proxy["username"] = unquote(parsed.username)
    if parsed.password is not None:
        proxy["password"] = unquote(parsed.password)
    proxy["udp"] = True
    _apply_standard_options(proxy, query)
    return proxy


def _parse_ss(uri: str) -> dict[str, Any]:
    body = uri[5:]
    fragment = ""
    if "#" in body:
        body, fragment = body.split("#", 1)
    query_text = ""
    if "?" in body:
        body, query_text = body.split("?", 1)
    encoded_userinfo = False
    if "@" not in body:
        decoded = decode_base64_text(body)
        if "@" not in decoded:
            raise SubscriptionError("ss URI has no server")
        body = decoded
        encoded_userinfo = True
    userinfo, endpoint = body.rsplit("@", 1)
    if ":" not in userinfo and not encoded_userinfo:
        userinfo = decode_base64_text(userinfo)
        encoded_userinfo = True
    if ":" not in userinfo:
        raise SubscriptionError("ss URI has no cipher or password")
    cipher, password = userinfo.split(":", 1)
    parsed = urlsplit(f"ss://x@{endpoint}")
    if not parsed.hostname:
        raise SubscriptionError("ss URI has no hostname")
    if parsed.path not in {"", "/"}:
        raise SubscriptionError("ss URI has an unsupported path")
    query = parse_qs(query_text, keep_blank_values=True)
    proxy: dict[str, Any] = {
        "name": unquote(fragment) or f"ss-{parsed.hostname}",
        "type": "ss",
        "server": parsed.hostname,
        "port": _port(parsed),
        "cipher": cipher if encoded_userinfo else unquote(cipher),
        "password": password if encoded_userinfo else unquote(password),
        "udp": True,
    }
    if set(query) - {"plugin"}:
        raise SubscriptionError("ss URI has unsupported URI parameter")
    plugin = _single_query(query, "plugin")
    if "plugin" in query:
        plugin_fields = plugin.split(";")
        plugin_name = plugin_fields[0]
        options: dict[str, str] = {}
        for part in plugin_fields[1:]:
            key, separator, value = part.partition("=")
            if not key or key in options:
                raise SubscriptionError("ss URI has unsupported URI parameter")
            if not separator:
                value = "true"
            options[key] = value
        if plugin_name == "obfs-local" and set(options) == {"obfs", "obfs-host"}:
            if options["obfs"] not in {"http", "tls"} or not options["obfs-host"]:
                raise SubscriptionError("ss URI has unsupported URI parameter")
            proxy["plugin"] = "obfs"
            proxy["plugin-opts"] = {"mode": options["obfs"], "host": options["obfs-host"]}
        elif plugin_name == "v2ray-plugin" and set(options) <= {"mode", "tls", "host", "path"}:
            if options.get("mode", "websocket") != "websocket" or options.get(
                "tls", "false"
            ) not in {
                "true",
                "false",
            }:
                raise SubscriptionError("ss URI has unsupported URI parameter")
            proxy["plugin"] = "v2ray-plugin"
            proxy["plugin-opts"] = {"mode": "websocket"}
            if options.get("tls") == "true":
                proxy["plugin-opts"]["tls"] = True
            for option in ("host", "path"):
                if option in options:
                    proxy["plugin-opts"][option] = options[option]
        else:
            raise SubscriptionError("ss URI has unsupported URI parameter")
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
        return _parse_vless(value)
    if lowered.startswith("trojan://"):
        return _parse_trojan(value)
    if lowered.startswith(("hysteria2://", "hy2://")):
        return _parse_hysteria2("hysteria2://" + value.split("://", 1)[1])
    if lowered.startswith("hysteria://"):
        return _parse_hysteria(value)
    if lowered.startswith("tuic://"):
        return _parse_tuic(value)
    if lowered.startswith("anytls://"):
        return _parse_anytls(value)
    if lowered.startswith("socks5://"):
        return _parse_socks5(value)
    if lowered.startswith("socks://"):
        return _parse_socks5("socks5://" + value[8:])
    if lowered.startswith("http://"):
        return _parse_http(value)
    if lowered.startswith("https://"):
        return _parse_http(value, tls=True)
    scheme = value.split(":", 1)[0] if ":" in value else "<missing>"
    raise SubscriptionError(f"unsupported proxy URI scheme: {scheme}")
