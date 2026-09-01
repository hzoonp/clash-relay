"""Private pre-publish transport qualification for general automatic egress.

P13 keeps manual node choice intact. It live-qualifies the general automatic
selector over HTTPS, then checks UDP transport through Mihomo's SOCKS5 UDP
associate path. A QUIC version-negotiation packet is attempted first; a DNS
UDP round-trip is the fallback. Media and messaging automatic selectors only
receive nodes with a live UDP path.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .util import atomic_write, dump_yaml, load_yaml_file
from .validator import validate_generated_config

_GENERAL_PROVIDER_PREFIX = "cr_general_"
_PROBE_GROUP = "__CR_TRANSPORT_QUALIFICATION"
_GENERAL_AUTO_GROUP = "自动选择"
_UDP_AUTO_GROUPS = ("媒体自动", "通讯自动")
_TCP_ATTEMPTS = 2
_TCP_REQUIRED_SUCCESSES = 1
_TCP_URL = "https://www.gstatic.com/generate_204"
_TCP_TIMEOUT_MS = 3000
_UDP_TIMEOUT_SECONDS = 0.7
_RE2_META = frozenset("\\.+*?()|[]{}^$")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _comment_header(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def _quote_re2_literal(value: str) -> str:
    return "".join(f"\\{character}" if character in _RE2_META else character for character in value)


def _exact_filter(names: set[str]) -> str:
    if not names:
        raise ValidationError("transport qualification cannot create an empty automatic filter")
    return "^(" + "|".join(_quote_re2_literal(name) for name in sorted(names)) + ")$"


def _general_provider_payloads(config: dict[str, Any]) -> dict[str, tuple[dict[str, Any], ...]]:
    providers = config.get("proxy-providers")
    if not isinstance(providers, dict):
        raise ValidationError("transport qualification requires proxy-providers")
    result: dict[str, tuple[dict[str, Any], ...]] = {}
    for provider_name in sorted(providers):
        if not str(provider_name).startswith(_GENERAL_PROVIDER_PREFIX):
            continue
        provider = providers[provider_name]
        payload = provider.get("payload") if isinstance(provider, dict) else None
        if not isinstance(payload, list) or not payload:
            raise ValidationError("general transport provider payload is invalid")
        normalized: list[dict[str, Any]] = []
        for proxy in payload:
            if not isinstance(proxy, dict) or not isinstance(proxy.get("name"), str):
                raise ValidationError("general transport provider contains an unnamed proxy")
            normalized.append(dict(proxy))
        result[str(provider_name)] = tuple(normalized)
    if not result:
        raise ValidationError("transport qualification found no general provider inventory")
    return result


def _temporary_probe_config(
    base_config: dict[str, Any],
    provider_payloads: dict[str, tuple[dict[str, Any], ...]],
    *,
    mixed_port: int,
    controller_port: int,
    secret: str,
) -> dict[str, Any]:
    original_providers = base_config.get("proxy-providers")
    if not isinstance(original_providers, dict):
        raise ValidationError("candidate proxy-providers must be a mapping")
    providers: dict[str, Any] = {}
    for provider_name, payload in provider_payloads.items():
        original = original_providers.get(provider_name)
        if not isinstance(original, dict):
            raise ValidationError("general provider disappeared during transport qualification")
        provider = {
            key: value for key, value in original.items() if key not in {"health-check", "payload"}
        }
        provider["type"] = "inline"
        provider["payload"] = [dict(proxy) for proxy in payload]
        providers[provider_name] = provider

    config: dict[str, Any] = {
        "mixed-port": mixed_port,
        "allow-lan": False,
        "bind-address": "127.0.0.1",
        "mode": "rule",
        "log-level": "warning",
        "ipv6": False,
        "external-controller": f"127.0.0.1:{controller_port}",
        "secret": secret,
        "proxy-providers": providers,
        "proxy-groups": [{"name": _PROBE_GROUP, "type": "select", "use": sorted(providers)}],
        "rules": [f"MATCH,{_PROBE_GROUP}"],
    }
    dns = base_config.get("dns")
    if isinstance(dns, dict):
        probe_dns = dict(dns)
        if probe_dns.get("enable"):
            probe_dns["listen"] = f"127.0.0.1:{_free_port()}"
        config["dns"] = probe_dns
    return config


def _controller_get(port: int, secret: str, path: str, *, timeout: float = 2.0) -> dict[str, Any]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        headers={"Authorization": f"Bearer {secret}"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValidationError("Mihomo transport controller returned an invalid response")
    return payload


def _controller_put(port: int, secret: str, path: str, payload: dict[str, Any]) -> None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        },
        method="PUT",
    )
    with urllib.request.urlopen(request, timeout=2.0) as response:
        if response.status not in {200, 204}:
            raise ValidationError("Mihomo rejected transport selector update")


def _wait_for_controller(process: subprocess.Popen[bytes], port: int, secret: str) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ValidationError("Mihomo exited before transport qualification could start")
        try:
            _controller_get(port, secret, "/version", timeout=0.5)
            return
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.1)
    raise ValidationError("Mihomo controller did not become ready for transport qualification")


def _wait_for_members(
    process: subprocess.Popen[bytes], port: int, secret: str, expected_names: set[str]
) -> None:
    encoded = urllib.parse.quote(_PROBE_GROUP, safe="")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ValidationError("Mihomo exited while loading transport providers")
        try:
            group = _controller_get(port, secret, f"/proxies/{encoded}", timeout=0.5)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.1)
            continue
        members = group.get("all")
        if isinstance(members, list) and expected_names.issubset({str(item) for item in members}):
            return
        time.sleep(0.1)
    raise ValidationError("transport qualification providers did not populate their selector")


def _group_delay_probe(port: int, secret: str) -> dict[str, int]:
    encoded = urllib.parse.quote(_PROBE_GROUP, safe="")
    query = urllib.parse.urlencode(
        {"url": _TCP_URL, "timeout": _TCP_TIMEOUT_MS, "expected": "204"}
    )
    try:
        result = _controller_get(
            port,
            secret,
            f"/group/{encoded}/delay?{query}",
            timeout=(_TCP_TIMEOUT_MS / 1000) + 3,
        )
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return {}
    return {
        str(name): delay
        for name, delay in result.items()
        if isinstance(name, str) and isinstance(delay, int) and delay >= 0
    }


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise OSError("unexpected SOCKS5 EOF")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_socks_address(sock: socket.socket, atyp: int) -> str:
    if atyp == 1:
        return socket.inet_ntop(socket.AF_INET, _recv_exact(sock, 4))
    if atyp == 4:
        return socket.inet_ntop(socket.AF_INET6, _recv_exact(sock, 16))
    if atyp == 3:
        length = _recv_exact(sock, 1)[0]
        return _recv_exact(sock, length).decode("ascii")
    raise OSError("unsupported SOCKS5 address type")


def _udp_relay(mixed_port: int) -> tuple[socket.socket, socket.socket, tuple[Any, ...]]:
    control = socket.create_connection(("127.0.0.1", mixed_port), timeout=2.0)
    control.settimeout(2.0)
    control.sendall(b"\x05\x01\x00")
    if _recv_exact(control, 2) != b"\x05\x00":
        control.close()
        raise OSError("SOCKS5 authentication negotiation failed")
    control.sendall(b"\x05\x03\x00\x01\x00\x00\x00\x00\x00\x00")
    head = _recv_exact(control, 4)
    if head[0] != 5 or head[1] != 0:
        control.close()
        raise OSError("SOCKS5 UDP associate failed")
    host = _read_socks_address(control, head[3])
    port = int.from_bytes(_recv_exact(control, 2), "big")
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    family, socktype, proto, _, sockaddr = socket.getaddrinfo(
        host, port, type=socket.SOCK_DGRAM
    )[0]
    udp = socket.socket(family, socktype, proto)
    udp.settimeout(_UDP_TIMEOUT_SECONDS)
    return control, udp, sockaddr


def _socks_udp_roundtrip(mixed_port: int, target: str, port: int, payload: bytes) -> bool:
    try:
        control, udp, relay = _udp_relay(mixed_port)
    except OSError:
        return False
    try:
        address = socket.inet_aton(target)
        packet = b"\x00\x00\x00\x01" + address + port.to_bytes(2, "big") + payload
        udp.sendto(packet, relay)
        response, _ = udp.recvfrom(4096)
        return len(response) > 10 and response[:3] == b"\x00\x00\x00"
    except (OSError, TimeoutError):
        return False
    finally:
        udp.close()
        control.close()


def _quic_probe_payload() -> bytes:
    # An unsupported-version long-header datagram asks a QUIC server for Version
    # Negotiation without requiring TLS keys. 1200 bytes avoids amplification
    # suppression on compliant servers.
    dcid = b"p13dcid1"
    scid = b"p13scid1"
    header = (
        b"\xc0"
        + (0x0A0A0A0A).to_bytes(4, "big")
        + bytes([len(dcid)])
        + dcid
        + bytes([len(scid)])
        + scid
    )
    return header.ljust(1200, b"\x00")


def _dns_probe_payload() -> bytes:
    qname = b"".join(bytes([len(label)]) + label for label in b"cloudflare.com".split(b"."))
    return b"\xc1\xa5\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + qname + b"\x00\x00\x01\x00\x01"


def _udp_transport_probe(mixed_port: int) -> tuple[bool, bool]:
    quic = _socks_udp_roundtrip(mixed_port, "1.1.1.1", 443, _quic_probe_payload())
    if quic:
        return True, True
    udp = _socks_udp_roundtrip(mixed_port, "1.1.1.1", 53, _dns_probe_payload())
    return udp, False


def probe_transport_nodes(
    binary: Path, candidate_path: Path, diagnostics: dict[str, Any] | None = None
) -> tuple[set[str], set[str], set[str]]:
    binary = binary.resolve()
    candidate_path = candidate_path.resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValidationError("transport qualification requires an executable Mihomo binary")
    config = load_yaml_file(candidate_path)
    if not isinstance(config, dict):
        raise ValidationError("candidate is not a YAML mapping")
    payloads = _general_provider_payloads(config)
    proxies = [proxy for payload in payloads.values() for proxy in payload]
    by_name = {str(proxy["name"]): proxy for proxy in proxies}
    if len(by_name) != len(proxies):
        raise ValidationError("transport qualification requires unique general runtime names")
    node_names = set(by_name)

    with tempfile.TemporaryDirectory(prefix="clash-relay-transport-") as temp_name:
        workdir = Path(temp_name)
        mixed_port = _free_port()
        controller_port = _free_port()
        secret = "clash-relay-transport-qualification-only"
        temporary = _temporary_probe_config(
            config,
            payloads,
            mixed_port=mixed_port,
            controller_port=controller_port,
            secret=secret,
        )
        probe_path = workdir / "probe.yaml"
        probe_path.write_text(dump_yaml(temporary), encoding="utf-8")
        try:
            test = subprocess.run(
                [str(binary), "-t", "-d", str(workdir), "-f", str(probe_path)],
                cwd=workdir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                timeout=30,
                check=False,
                env={**os.environ, "TZ": "UTC"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValidationError("failed to execute Mihomo for transport qualification") from exc
        if test.returncode != 0:
            raise ValidationError("Mihomo rejected the transport qualification configuration")

        try:
            process = subprocess.Popen(
                [str(binary), "-d", str(workdir), "-f", str(probe_path)],
                cwd=workdir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                env={**os.environ, "TZ": "UTC"},
                start_new_session=True,
            )
        except OSError as exc:
            raise ValidationError("failed to start Mihomo for transport qualification") from exc
        try:
            _wait_for_controller(process, controller_port, secret)
            _wait_for_members(process, controller_port, secret, node_names)

            tcp_samples = [_group_delay_probe(controller_port, secret) for _ in range(_TCP_ATTEMPTS)]
            tcp_counts = {
                name: sum(1 for sample in tcp_samples if name in sample) for name in sorted(node_names)
            }
            tcp_qualified = {
                name for name, count in tcp_counts.items() if count >= _TCP_REQUIRED_SUCCESSES
            }
            if not tcp_qualified:
                raise ValidationError("no general nodes passed live HTTPS transport qualification")

            encoded_group = urllib.parse.quote(_PROBE_GROUP, safe="")
            udp_qualified: set[str] = set()
            quic_path: set[str] = set()
            static_udp_disabled = 0
            selector_failures = 0
            for name in sorted(tcp_qualified):
                proxy = by_name[name]
                if proxy.get("udp") is False:
                    static_udp_disabled += 1
                    continue
                try:
                    _controller_put(
                        controller_port,
                        secret,
                        f"/proxies/{encoded_group}",
                        {"name": name},
                    )
                except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
                    selector_failures += 1
                    continue
                udp_ok, quic_ok = _udp_transport_probe(mixed_port)
                if udp_ok:
                    udp_qualified.add(name)
                if quic_ok:
                    quic_path.add(name)
        finally:
            if process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)

    if not udp_qualified:
        raise ValidationError(
            "no general nodes passed UDP transport qualification; refusing to publish unsafe automatic media/messaging selectors"
        )
    if diagnostics is not None:
        diagnostics.clear()
        diagnostics.update(
            {
                "qualification_mode": "pre_publish_transport",
                "tested_nodes": len(node_names),
                "tcp_qualified_nodes": len(tcp_qualified),
                "udp_qualified_nodes": len(udp_qualified),
                "quic_path_nodes": len(quic_path),
                "tcp_failed_nodes": len(node_names - tcp_qualified),
                "udp_failed_nodes": len(tcp_qualified - udp_qualified),
                "static_udp_disabled_nodes": static_udp_disabled,
                "selector_failures": selector_failures,
                "tcp_attempts": _TCP_ATTEMPTS,
                "tcp_required_successes": _TCP_REQUIRED_SUCCESSES,
                "udp_timeout_ms": int(_UDP_TIMEOUT_SECONDS * 1000),
            }
        )
    return tcp_qualified, udp_qualified, quic_path


def _rewrite_group(config: dict[str, Any], name: str, allowed_names: set[str]) -> None:
    groups = config.get("proxy-groups")
    if not isinstance(groups, list):
        raise ValidationError("candidate proxy group structure is invalid")
    group = next(
        (item for item in groups if isinstance(item, dict) and item.get("name") == name),
        None,
    )
    if not isinstance(group, dict) or group.get("type") != "url-test":
        raise ValidationError(f"transport qualification requires url-test group {name!r}")
    current_filter = group.get("filter")
    if current_filter not in (None, "", ".*"):
        raise ValidationError(f"transport qualification cannot compose custom filter for {name!r}")
    group["filter"] = _exact_filter(allowed_names)


def apply_transport_qualification(
    config: dict[str, Any], tcp_qualified: set[str], udp_qualified: set[str]
) -> dict[str, Any]:
    udp_effective = udp_qualified & tcp_qualified
    if not tcp_qualified:
        raise ValidationError("transport qualification received no TCP-qualified nodes")
    if not udp_effective:
        raise ValidationError("transport qualification received no UDP-qualified nodes")
    _rewrite_group(config, _GENERAL_AUTO_GROUP, tcp_qualified)
    for group_name in _UDP_AUTO_GROUPS:
        _rewrite_group(config, group_name, udp_effective)
    return {
        "general_automatic_nodes": len(tcp_qualified),
        "udp_automatic_nodes": len(udp_effective),
        "general_group": _GENERAL_AUTO_GROUP,
        "udp_groups": list(_UDP_AUTO_GROUPS),
    }


def rewrite_transport_qualified_candidate(
    candidate_path: Path, tcp_qualified: set[str], udp_qualified: set[str]
) -> dict[str, Any]:
    try:
        original = candidate_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError("failed to read candidate for transport qualification") from exc
    config = load_yaml_file(candidate_path)
    if not isinstance(config, dict):
        raise ValidationError("candidate is not a YAML mapping")
    report = apply_transport_qualification(config, tcp_qualified, udp_qualified)
    validate_generated_config(config)
    atomic_write(candidate_path, _comment_header(original) + dump_yaml(config))
    return report
