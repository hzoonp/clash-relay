from __future__ import annotations

import os
import socket
from pathlib import Path
from socketserver import BaseRequestHandler, ThreadingUDPServer
from threading import Thread

import pytest
from _mihomo import mihomo_session, serving_udp, wait_until

from clash_relay.util import dump_yaml

pytestmark = pytest.mark.integration


class _UdpEcho(BaseRequestHandler):
    def handle(self) -> None:
        data, sock = self.request
        sock.sendto(data, self.client_address)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    data = b""
    while len(data) < length:
        part = sock.recv(length - len(data))
        if not part:
            raise AssertionError("SOCKS5 control channel closed")
        data += part
    return data


def _udp_request(proxy_port: int, host: str, destination_port: int) -> bytes | None:
    with socket.create_connection(("127.0.0.1", proxy_port), timeout=3) as control:
        control.settimeout(3)
        control.sendall(b"\x05\x01\x00")
        assert _recv_exact(control, 2) == b"\x05\x00"
        control.sendall(b"\x05\x03\x00\x01\x00\x00\x00\x00\x00\x00")
        header = _recv_exact(control, 4)
        assert header[:2] == b"\x05\x00"
        if header[3] == 1:
            address = socket.inet_ntoa(_recv_exact(control, 4))
        elif header[3] == 3:
            size = _recv_exact(control, 1)[0]
            address = _recv_exact(control, size).decode("ascii")
        else:
            raise AssertionError("unexpected SOCKS5 relay address type")
        relay_port = int.from_bytes(_recv_exact(control, 2), "big")
        if address == "0.0.0.0":
            address = "127.0.0.1"
        host_bytes = host.encode("ascii")
        payload = b"udp-download-isolation"
        packet = (
            b"\x00\x00\x00\x03"
            + bytes([len(host_bytes)])
            + host_bytes
            + destination_port.to_bytes(2, "big")
            + payload
        )
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.settimeout(1.2)
            udp.sendto(packet, (address, relay_port))
            try:
                response, _ = udp.recvfrom(4096)
            except TimeoutError:
                return None
        assert response[:3] == b"\x00\x00\x00"
        return response[-len(payload) :]


def _run_case(
    binary: str, root: Path, destination_port: int, *, guard: bool
) -> tuple[bytes | None, bytes | None]:
    root.mkdir()
    proxy_port = _free_port()
    rules = ["DOMAIN-SUFFIX,gvt1.com,下载流量"]
    if guard:
        rules.append("DOMAIN-SUFFIX,gvt1.com,REJECT")
    rules.extend(
        [
            "DOMAIN-SUFFIX,gvt1.com,网页浏览",
            "DOMAIN,safe.example.test,DIRECT",
            "MATCH,REJECT",
        ]
    )
    config = {
        "mixed-port": proxy_port,
        "mode": "rule",
        "log-level": "debug",
        "profile": {"store-selected": False},
        "hosts": {"assets.gvt1.com": "127.0.0.1", "safe.example.test": "127.0.0.1"},
        "proxies": [{"name": "http-only", "type": "http", "server": "127.0.0.1", "port": 29999}],
        "proxy-groups": [
            {"name": "下载流量", "type": "select", "proxies": ["http-only"]},
            {"name": "网页浏览", "type": "select", "proxies": ["DIRECT"]},
        ],
        "rules": rules,
    }
    path = root / "config.yaml"
    path.write_text(dump_yaml(config), encoding="utf-8")
    with mihomo_session(binary, path, root) as mihomo:

        def ready() -> bool:
            result = _udp_request(proxy_port, "safe.example.test", destination_port)
            if result != b"udp-download-isolation":
                raise AssertionError(f"safe SOCKS5 UDP probe returned {result!r}")
            return True

        wait_until(
            ready,
            timeout=12,
            label=f"Mihomo SOCKS5 UDP route 127.0.0.1:{proxy_port}",
            mihomo=mihomo,
        )
        control = _udp_request(proxy_port, "safe.example.test", destination_port)
        classified = _udp_request(proxy_port, "assets.gvt1.com", destination_port)
        assert control == b"udp-download-isolation", mihomo.failure_context()
        return control, classified


def test_unsupported_udp_download_cannot_continue_to_browsing(tmp_path: Path) -> None:
    binary = os.environ.get("MIHOMO_BIN")
    if not binary:
        pytest.skip("MIHOMO_BIN is not set")
    server = ThreadingUDPServer(("127.0.0.1", 0), _UdpEcho)
    thread = Thread(target=server.serve_forever, daemon=True)
    with serving_udp(server, thread):
        port = int(server.server_address[1])
        without_guard = _run_case(binary, tmp_path / "without-guard", port, guard=False)
        with_guard = _run_case(binary, tmp_path / "with-guard", port, guard=True)
        assert without_guard == (b"udp-download-isolation", b"udp-download-isolation")
        assert with_guard == (b"udp-download-isolation", None)
