from __future__ import annotations

import contextlib
import json
import os
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from _mihomo import RunningMihomo, start_mihomo, wait_until

from clash_relay.subscription_parser import parse_subscription
from clash_relay.util import dump_yaml

pytestmark = pytest.mark.integration
FIXTURES = Path(__file__).parents[1] / "fixtures" / "uri"
RUNTIME_MATRIX = {
    "vless-reality": "tcp",
    "trojan": "tcp",
    "hysteria2": "udp",
    "tuic": "udp",
    "anytls": "tcp",
}


def _binary() -> Path:
    value = os.environ.get("MIHOMO_BIN")
    if not value:
        pytest.skip("MIHOMO_BIN is not set")
    return Path(value).resolve()


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def _capture_transport(transport: str) -> Iterator[tuple[int, threading.Event]]:
    socket_type = socket.SOCK_STREAM if transport == "tcp" else socket.SOCK_DGRAM
    listener = socket.socket(socket.AF_INET, socket_type)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    if transport == "tcp":
        listener.listen(4)
    listener.settimeout(0.2)

    observed = threading.Event()
    stopping = threading.Event()

    def run() -> None:
        while not stopping.is_set():
            try:
                if transport == "tcp":
                    connection, _ = listener.accept()
                    with connection:
                        connection.settimeout(1)
                        payload = connection.recv(4096)
                else:
                    payload, _ = listener.recvfrom(4096)
            except (OSError, TimeoutError):
                continue
            if payload:
                observed.set()
                return

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        yield int(listener.getsockname()[1]), observed
    finally:
        stopping.set()
        listener.close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def _controller_proxy(controller_port: int, proxy_name: str) -> dict:
    encoded = urllib.parse.quote(proxy_name, safe="")
    with urllib.request.urlopen(
        f"http://127.0.0.1:{controller_port}/proxies/{encoded}",
        timeout=1,
    ) as response:
        payload = json.load(response)
    assert isinstance(payload, dict)
    return payload


def _wait_controller(mihomo: RunningMihomo, controller_port: int, proxy_name: str) -> None:
    wait_until(
        lambda: bool(_controller_proxy(controller_port, proxy_name)),
        timeout=15,
        label=f"Mihomo controller 127.0.0.1:{controller_port}",
        mihomo=mihomo,
    )


def _trigger_delay(controller_port: int, proxy_name: str) -> None:
    encoded = urllib.parse.quote(proxy_name, safe="")
    probe_url = urllib.parse.quote("https://www.gstatic.com/generate_204", safe="")
    request = urllib.request.Request(
        f"http://127.0.0.1:{controller_port}/proxies/{encoded}/delay?url={probe_url}&timeout=2500"
    )
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        exc.read()
    except urllib.error.URLError:
        pass


@pytest.mark.parametrize("fixture_name,transport", RUNTIME_MATRIX.items())
def test_protocol_fixture_starts_and_initiates_transport(
    fixture_name: str,
    transport: str,
    tmp_path: Path,
) -> None:
    fixture = yaml.safe_load((FIXTURES / f"{fixture_name}.yaml").read_text(encoding="utf-8"))
    proxy = parse_subscription(fixture["uri"]).proxies[0]

    with _capture_transport(transport) as (upstream_port, observed):
        proxy["server"] = "127.0.0.1"
        proxy["port"] = upstream_port
        controller_port = _port()
        config = {
            "mixed-port": _port(),
            "allow-lan": False,
            "mode": "rule",
            "log-level": "warning",
            "external-controller": f"127.0.0.1:{controller_port}",
            "proxies": [proxy],
            "proxy-groups": [
                {
                    "name": "Fixture",
                    "type": "select",
                    "proxies": [proxy["name"]],
                }
            ],
            "rules": ["MATCH,Fixture"],
        }
        workdir = tmp_path / fixture_name
        workdir.mkdir()
        target = workdir / "config.yaml"
        target.write_text(dump_yaml(config), encoding="utf-8")

        mihomo = start_mihomo(_binary(), target, workdir)
        try:
            _wait_controller(mihomo, controller_port, str(proxy["name"]))
            runtime_proxy = _controller_proxy(controller_port, str(proxy["name"]))
            assert str(runtime_proxy.get("type", "")).lower().replace("-", "") == str(
                proxy["type"]
            ).lower().replace("-", "")

            _trigger_delay(controller_port, str(proxy["name"]))
            wait_until(
                observed.is_set,
                timeout=5,
                label=f"{fixture_name} {transport} transport initiation",
                mihomo=mihomo,
            )
        finally:
            mihomo.stop()
