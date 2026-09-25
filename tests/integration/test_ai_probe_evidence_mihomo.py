"""Real-Mihomo regression tests for AI probe endpoint evidence.

These tests exercise the genuine production path -- ``probe_ai_nodes`` with a
live Mihomo core, a local HTTP server, and shards -- with no monkeypatching of
the prober, so region endpoint evidence (probed/passed/failed/reached/
network_failure/outcomes) is validated exactly as production aggregates it.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from clash_relay.ai_qualification import probe_ai_nodes

pytestmark = pytest.mark.integration


def _binary() -> Path:
    import os

    value = os.environ.get("MIHOMO_BIN")
    if not value:
        pytest.skip("MIHOMO_BIN is not set")
    return Path(value)


class _ProbeHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        if self.path.startswith("/wrong"):
            self.send_response(200)  # HTTP ok, but not the expected 204
        elif self.path.startswith("/blocked"):
            self.send_response(403)
        else:
            self.send_response(204)
        self.end_headers()

    def log_message(self, *_args):
        pass


def _server() -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProbeHandler)
    thread = None
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def _candidate(
    path: Path, *, nodes_per_provider: int, control_port: int, blocked_port: int
) -> dict:
    providers = {}
    for region in ("jp", "sg"):
        providers[f"cr_ai_{region}_{region}"] = {
            "type": "inline",
            "payload": [
                {
                    "name": f"[AI:{region.upper()}] sub_5/node-{region}-{index}",
                    "type": "direct",
                }
                for index in range(1, nodes_per_provider + 1)
            ],
        }
    candidate = {
        "mixed-port": 7890,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "warning",
        # Pin probe hosts to loopback so mihomo never has to resolve the
        # synthetic names and never substitutes its own gateway errors.
        "hosts": {
            "ai-probe.invalid": "127.0.0.1",
            "ai-blocked.invalid": "127.0.0.1",
        },
        "proxy-providers": providers,
        "proxy-groups": [
            {
                "name": "__CR_AI_PROBE",
                "type": "select",
                "use": list(providers),
            }
        ],
        "rules": ["MATCH,__CR_AI_PROBE"],
    }
    path.write_text(yaml.safe_dump(candidate, sort_keys=False), encoding="utf-8")
    return candidate


def _closed_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _regions_map(candidate_path: Path) -> dict[str, str]:
    config = yaml.safe_load(candidate_path.read_text(encoding="utf-8"))
    # Derive regions from the generated provider identity directly.
    provider_regions = {
        provider_name: provider_name.rsplit("_", 1)[-1].upper()
        for provider_name in config["proxy-providers"]
        if str(provider_name).startswith("cr_ai_")
    }
    assert provider_regions == {"cr_ai_jp_jp": "JP", "cr_ai_sg_sg": "SG"}
    for provider_name in provider_regions:
        assert provider_name in config["proxy-providers"]
    return provider_regions


def test_region_endpoint_evidence_survives_multi_shard_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, port = _server()
    import threading

    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        candidate_path = tmp_path / "candidate.yaml"
        _candidate(
            candidate_path, nodes_per_provider=13, control_port=port, blocked_port=0
        )  # 26 nodes -> 2 shards
        provider_regions = _regions_map(candidate_path)

        diagnostics: dict = {}
        qualified = probe_ai_nodes(
            _binary(),
            candidate_path,
            (
                {
                    "name": "local",
                    "url": f"http://ai-probe.invalid:{port}/ok",
                    "method": "HEAD",
                    "expected_status": "204",
                    "timeout": 2000,
                },
            ),
            workers=2,
            diagnostics=diagnostics,
            provider_regions=provider_regions,
        )

        assert len(qualified) == 26
        regions = diagnostics["regions"]
        assert set(regions) == {"JP", "SG"}
        for row in regions.values():
            assert row["tested"] == 13
            assert row["qualified"] == 13
            endpoint = row["endpoints"]["local"]
            # 13 nodes -> 2 shards (20-node shard size) -> merged probed == 13.
            assert endpoint["probed"] == 13
            assert endpoint["passed"] == 13
            assert endpoint["failed"] == 0
            assert endpoint["reached"] == 13
            assert endpoint["network_failure"] == 0
            assert endpoint["outcomes"] == {"status_204": 13}
    finally:
        server.shutdown()


def test_status_200_against_expected_204_is_control_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, port = _server()
    import threading

    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        candidate_path = tmp_path / "candidate.yaml"
        _candidate(candidate_path, nodes_per_provider=1, control_port=port, blocked_port=0)
        provider_regions = _regions_map(candidate_path)

        diagnostics: dict = {}
        qualified = probe_ai_nodes(
            _binary(),
            candidate_path,
            (
                {
                    "name": "connectivity",
                    "url": f"http://ai-probe.invalid:{port}/blocked",
                    "method": "HEAD",
                    # Control expects 204; the server answers 403.
                    "expected_status": "204",
                    "timeout": 2000,
                },
            ),
            workers=1,
            diagnostics=diagnostics,
            provider_regions=provider_regions,
        )

        assert qualified == set()
        for row in diagnostics["regions"].values():
            endpoint = row["endpoints"]["connectivity"]
            # The probe's own expected_status (204) decides: a 403 is failed,
            # never a control success -- and it *was* reached (HTTP received).
            assert endpoint["passed"] == 0
            assert endpoint["failed"] == 1
            assert endpoint["reached"] == 1
            assert endpoint["network_failure"] == 0
            assert endpoint["outcomes"] == {"status_403": 1}
    finally:
        server.shutdown()


def test_status_200_against_expected_204_control_is_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, port = _server()
    import threading

    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        candidate_path = tmp_path / "candidate.yaml"
        _candidate(candidate_path, nodes_per_provider=1, control_port=port, blocked_port=0)
        provider_regions = _regions_map(candidate_path)

        diagnostics: dict = {}
        qualified = probe_ai_nodes(
            _binary(),
            candidate_path,
            (
                {
                    "name": "connectivity",
                    "url": f"http://ai-probe.invalid:{port}/wrong",
                    "method": "HEAD",
                    # Control expects 204; the server answers 200.
                    "expected_status": "204",
                    "timeout": 2000,
                },
            ),
            workers=1,
            diagnostics=diagnostics,
            provider_regions=provider_regions,
        )

        assert qualified == set()
        for row in diagnostics["regions"].values():
            endpoint = row["endpoints"]["connectivity"]
            assert endpoint["passed"] == 0
            assert endpoint["failed"] == 1
            assert endpoint["reached"] == 1
            assert endpoint["network_failure"] == 0
            assert endpoint["outcomes"] == {"status_200": 1}
    finally:
        server.shutdown()


def test_real_systemic_detection_end_to_end(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real probe diagnostics -> merge -> evaluate_endpoint_blockage must
    yield a systemic verdict with zero monkeypatching of the probe path."""

    from clash_relay.ai_application import _run_sentinel_gate

    server, port = _server()
    import threading

    threading.Thread(target=server.serve_forever, daemon=True).start()
    blocked_port = _closed_port()
    try:
        candidate_path = tmp_path / "candidate.yaml"
        _candidate(
            candidate_path,
            nodes_per_provider=2,
            control_port=port,
            blocked_port=blocked_port,
        )
        provider_regions = _regions_map(candidate_path)
        candidate = yaml.safe_load(candidate_path.read_text(encoding="utf-8"))

        # OpenAI critical endpoints target a bound-then-closed loopback port:
        # connection refused (network failure) through every region.
        # Connectivity control targets the working local server.
        primary = {
            "name": "ai_openai",
            "url": f"http://ai-blocked.invalid:{blocked_port}/blocked",
            "method": "HEAD",
            "expected_status": "200-399",
            "timeout": 1000,
        }
        critical = [
            primary,
            {
                "name": "openai_app_android",
                "url": f"http://ai-blocked.invalid:{blocked_port}/blocked-android",
                "method": "HEAD",
                "expected_status": "200-499",
                "timeout": 1000,
            },
        ]
        control = {
            "name": "connectivity",
            "url": f"http://ai-probe.invalid:{port}/ok",
            "method": "HEAD",
            "expected_status": "204",
            "timeout": 2000,
        }

        gate = _run_sentinel_gate(
            candidate=candidate_path,
            mihomo_bin=_binary(),
            qualification_probes=tuple(critical),
            control_probe=control,
            node_regions={
                str(proxy["name"]): region
                for provider_name, region in provider_regions.items()
                for proxy in candidate["proxy-providers"][provider_name]["payload"]
                if isinstance(proxy, dict)
            },
            provider_regions=provider_regions,
            workers=2,
        )

        # Dump the aggregate evidence for failure diagnostics.
        print("GATE-STATS:", json.dumps(gate["region_endpoint_stats"]))
        assert gate["ran"] is True
        assert gate["systemic"] is True
        assert sorted(gate["blocked_critical_endpoints"]) == [
            "ai_openai",
            "openai_app_android",
        ]
        assert gate["dominant_failure_category"] == "timeout"
        assert sorted(gate["control_ok_regions"]) == ["JP", "SG"]
    finally:
        server.shutdown()
