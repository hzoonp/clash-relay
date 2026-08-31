from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from clash_relay.browsing_qualification import probe_browsing_nodes

pytestmark = pytest.mark.integration


def _binary() -> Path:
    value = os.environ.get("MIHOMO_BIN")
    if not value:
        pytest.skip("MIHOMO_BIN is not set")
    return Path(value).resolve()


def test_real_mihomo_browsing_qualification_uses_provider_backed_group_delay(
    tmp_path: Path,
) -> None:
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def _ok(self) -> None:
            requests.append(self.command)
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_HEAD = _ok
        do_GET = _ok

        def log_message(self, format, *args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        candidate = {
            "mixed-port": 7890,
            "allow-lan": False,
            "mode": "rule",
            "log-level": "warning",
            "hosts": {"browsing-probe.invalid": "127.0.0.1"},
            "proxy-providers": {
                "cr_browsing_any": {
                    "type": "inline",
                    "payload": [{"name": "Browsing Direct", "type": "direct"}],
                }
            },
            "proxy-groups": [
                {
                    "name": "Browsing Test",
                    "type": "select",
                    "use": ["cr_browsing_any"],
                }
            ],
            "rules": ["MATCH,Browsing Test"],
        }
        path = tmp_path / "candidate.yaml"
        path.write_text(yaml.safe_dump(candidate, sort_keys=False), encoding="utf-8")
        url = f"http://browsing-probe.invalid:{server.server_port}/probe"
        diagnostics: dict = {}

        qualified = probe_browsing_nodes(
            _binary(),
            path,
            {
                "name": "local",
                "url": url,
                "expected_status": "204",
                "timeout": 2000,
            },
            workers=1,
            attempts=3,
            required_successes=2,
            diagnostics=diagnostics,
        )

        assert qualified == {"Browsing Direct"}, {
            "diagnostics": diagnostics,
            "requests": requests,
        }
        assert len(requests) == 3
        assert diagnostics["tested_nodes"] == 1
        assert diagnostics["qualified_nodes"] == 1
        assert diagnostics["failed_nodes"] == 0
        assert diagnostics["successful_samples"] == 3
        assert diagnostics["failed_samples"] == 0
        assert diagnostics["outcomes"]["success"] == 3
        assert diagnostics["qualified_latency_ms"]["p50"] > 0
    finally:
        server.shutdown()
        server.server_close()
