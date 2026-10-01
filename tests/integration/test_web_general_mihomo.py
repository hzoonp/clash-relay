from __future__ import annotations

import json
import os
import shutil
import socket
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

import pytest
from _mihomo import mihomo_session, serving_http, wait_until

from clash_relay.browsing_qualification import probe_browsing_nodes
from clash_relay.browsing_runtime import rewrite_hardened_browsing_qualified_candidate
from clash_relay.builder import build_candidate
from clash_relay.config_loader import load_project
from clash_relay.production_audit import audit_production_candidate
from clash_relay.runtime_graph import RuntimeGraph
from clash_relay.util import dump_yaml, load_yaml_file
from clash_relay.web_general_runtime import rewrite_web_general_qualified_candidate

pytestmark = pytest.mark.integration


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _Proxy:
    def __init__(self, marker: bytes, *, reserve: bool = False) -> None:
        self.marker = marker
        self.reserve = reserve
        self.probes = 0
        self.failed = False
        self.arrived = Event()
        self.release = Event()
        self.pages = 0
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def respond(self) -> None:
                path = urllib.parse.urlsplit(self.path).path
                if path == "/__health":
                    status, body = 200, b"ready"
                elif path == "/probe":
                    fixture.probes += 1
                    if fixture.reserve and fixture.probes == 1:
                        self.close_connection = True
                        return
                    status = 204
                    body = b""
                elif fixture.failed:
                    self.close_connection = True
                    return
                elif path == "/page":
                    fixture.pages += 1
                    fixture.arrived.set()
                    if not fixture.release.wait(timeout=5):
                        status, body = 504, b"fixture release deadline"
                    else:
                        status, body = 200, fixture.marker
                elif path == "/generate_204":
                    status, body = 204, b""
                else:
                    status, body = 200, fixture.marker
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            do_GET = respond
            do_HEAD = respond

            def do_CONNECT(self) -> None:
                self.send_response(200)
                self.end_headers()
                request = self.rfile.readline(65536).decode("ascii").strip().split()
                if len(request) != 3:
                    return
                self.command, self.path, _version = request
                while self.rfile.readline(65536) not in (b"\r\n", b"\n", b""):
                    pass
                self.respond()
                self.close_connection = True

            def log_message(self, _format: str, *_args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self.server.server_port)


def _request(port: int, path: str) -> tuple[int, bytes]:
    connection = HTTPConnection("127.0.0.1", port, timeout=7)
    try:
        connection.request("GET", f"http://web.fixture.test{path}")
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def _api(port: int, path: str) -> dict:
    connection = HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        assert response.status == 200
        return json.loads(response.read())
    finally:
        connection.close()


def test_compiled_proxylite_uses_general_stable_and_reserve_without_sub_1(
    repo_root: Path, tmp_path: Path
) -> None:
    binary = os.environ.get("MIHOMO_BIN")
    if not binary:
        pytest.fail("MIHOMO_BIN is required for the General web E2E")
    trap, stable, reserve = (
        _Proxy(b"SUB_1_TRAP"),
        _Proxy(b"GENERAL_STABLE"),
        _Proxy(b"GENERAL_RESERVE", reserve=True),
    )
    with ExitStack() as cleanup:
        for fixture in (trap, stable, reserve):
            cleanup.enter_context(serving_http(fixture.server, fixture.thread))
            cleanup.callback(fixture.release.set)
        root = tmp_path / "project"
        root.mkdir()
        for name in ("config.yaml", "subscriptions.yaml", "policies.yaml"):
            shutil.copy2(repo_root / name, root / name)
        for name in ("rules", "policies"):
            shutil.copytree(repo_root / name, root / name)
        declaration = load_yaml_file(root / "config.yaml")
        declaration["generation"]["reject_private_proxy_hosts"] = False
        (root / "config.yaml").write_text(dump_yaml(declaration), encoding="utf-8")
        subscriptions = load_yaml_file(root / "subscriptions.yaml")
        for row in subscriptions["subscriptions"]:
            row["enabled"] = row["id"] in {"subscription_1", "subscription_2"}
        (root / "subscriptions.yaml").write_text(dump_yaml(subscriptions), encoding="utf-8")
        paths = {
            "config_path": root / "config.yaml",
            "subscriptions_path": root / "subscriptions.yaml",
            "policies_path": root / "policies.yaml",
        }

        def subscription(url: str, **_kwargs) -> str:
            fixtures = (
                [("US Trap", trap)]
                if url.endswith("/1")
                else [("US Stable", stable), ("US Reserve", reserve)]
            )
            return dump_yaml(
                {
                    "proxies": [
                        {"name": name, "type": "http", "server": "127.0.0.1", "port": fixture.port}
                        for name, fixture in fixtures
                    ]
                }
            )

        result = build_candidate(
            **paths,
            env={
                "CLASH_RELAY_SUBSCRIPTIONS": json.dumps(
                    {f"SUBSCRIPTION_{i}_URL": f"https://fixture.invalid/{i}" for i in (1, 2)}
                )
            },
            fetcher=subscription,
            rule_fetcher=lambda *_a, **_k: "DOMAIN,web.fixture.test\n",
        )
        config = result.config
        assert not any(
            str(name).startswith("cr_web_general_")
            for name in config["proxy-providers"]
        )
        assert "__CR_WEB_GENERAL_INVENTORY" not in {
            str(group["name"]) for group in config["proxy-groups"]
        }
        config.pop("dns", None)
        path = root / "candidate.yaml"
        path.write_text(dump_yaml(config), encoding="utf-8")
        diagnostics: dict = {}
        qualified, stable_names = probe_browsing_nodes(
            Path(binary),
            path,
            {
                "name": "local",
                "url": f"http://127.0.0.1:{stable.port}/probe",
                "expected_status": "204",
                "timeout": 1500,
            },
            attempts=3,
            required_successes=2,
            diagnostics=diagnostics,
        )
        assert diagnostics["unique_nodes_probed"] == 3
        assert diagnostics["duplicate_probe_entries_avoided"] == 2
        assert stable.probes == reserve.probes == trap.probes == 3, diagnostics
        assert any("/US Stable " in name for name in stable_names), {
            "stable": stable_names,
            "diagnostics": diagnostics,
        }
        assert not any("/US Reserve " in name for name in stable_names), {
            "stable": stable_names,
            "diagnostics": diagnostics,
        }
        rewrite_hardened_browsing_qualified_candidate(path, qualified, stable_names)
        report = rewrite_web_general_qualified_candidate(
            path, qualified, stable_names, stable_names
        )
        config = load_yaml_file(path)
        assert report["qualification"] == "passed"
        audit = audit_production_candidate(load_project(**paths), config)
        assert audit["web_general"]["subscription_1_reachable"] is False
        assert RuntimeGraph.from_candidate(config).walk_resolved(
            "网页通用自动"
        ).providers == frozenset({"cr_general_any"})

        # Local probe URLs and controller belong only to this E2E runtime.
        workdir = tmp_path / "runtime"
        workdir.mkdir()
        mixed, controller = _port(), _port()
        assert mixed != controller
        config.update(
            {
                "mixed-port": mixed,
                "external-controller": f"127.0.0.1:{controller}",
                "log-level": "debug",
                "profile": {"store-selected": False},
            }
        )
        config.pop("listeners", None)
        probe_url = f"http://127.0.0.1:{stable.port}/generate_204"
        for group in config["proxy-groups"]:
            if group["type"] in {"url-test", "fallback"}:
                group.update(url=probe_url, interval=1, timeout=500, lazy=False)
        for provider in config["proxy-providers"].values():
            provider["health-check"].update(url=probe_url, interval=1, timeout=500, lazy=False)
        generic = "RULE-SET,acl4ssr_proxy_lite,网页通用自动"
        assert generic in config["rules"]
        config["rules"] = [generic, "MATCH,REJECT"]
        config["rule-providers"] = {
            "acl4ssr_proxy_lite": {
                "type": "inline",
                "behavior": "classical",
                "payload": ["DOMAIN,web.fixture.test"],
            }
        }
        runtime_path = workdir / "config.yaml"
        runtime_path.write_text(dump_yaml(config), encoding="utf-8")
        with mihomo_session(binary, runtime_path, workdir) as mihomo:
            wait_until(
                lambda: _request(mixed, "/ready") == (200, stable.marker),
                timeout=12,
                label="compiled General web stable route",
                mihomo=mihomo,
            )

            def check_page(fixture: _Proxy, tier: str) -> None:
                fixture.arrived.clear()
                fixture.release.clear()
                try:
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        request = pool.submit(_request, mixed, "/page")
                        assert fixture.arrived.wait(timeout=3), mihomo.failure_context()
                        chains: list[str] = []

                        def observed() -> bool:
                            for row in _api(controller, "/connections")["connections"]:
                                if row["metadata"].get("host") == "web.fixture.test":
                                    chains[:] = row["chains"]
                                    return True
                            return False

                        wait_until(
                            observed, timeout=2, label="active ProxyLite connection", mihomo=mihomo
                        )
                        assert "网页通用自动" in chains and "网页通用 · 美国" in chains, chains
                        assert tier in chains, chains
                        assert any("sub_2/" in name for name in chains), chains
                        assert all("sub_1/" not in name for name in chains), chains
                        fixture.release.set()
                        assert request.result(timeout=3) == (200, fixture.marker), (
                            mihomo.failure_context()
                        )
                finally:
                    fixture.release.set()

            check_page(stable, "__CR_WEB_GENERAL_US_STABLE_AUTO")
            stable.failed = True
            wait_until(
                lambda: _request(mixed, "/ready") == (200, reserve.marker),
                timeout=12,
                label="compiled General web reserve failover",
                mihomo=mihomo,
            )
            check_page(reserve, "__CR_WEB_GENERAL_US_RESERVE_AUTO")
            assert trap.pages == 0, mihomo.failure_context()
