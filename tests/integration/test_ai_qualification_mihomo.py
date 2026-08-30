from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from clash_relay.ai_qualification import probe_ai_nodes

pytestmark = pytest.mark.integration


def _binary() -> Path:
    value = os.environ.get("MIHOMO_BIN")
    if not value:
        pytest.skip("MIHOMO_BIN is not set")
    return Path(value).resolve()


def test_real_mihomo_ai_qualification_honors_expected_status(tmp_path: Path) -> None:
    observed: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def _ok(self) -> None:
            observed.append(self.command)
            time.sleep(0.03)
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
            "hosts": {"ai-probe.invalid": "127.0.0.1"},
            "proxy-providers": {
                "cr_ai_test_test": {
                    "type": "inline",
                    "payload": [{"name": "AI Direct", "type": "direct"}],
                }
            },
            "proxy-groups": [{"name": "AI Test", "type": "select", "use": ["cr_ai_test_test"]}],
            "rules": ["MATCH,AI Test"],
        }
        path = tmp_path / "candidate.yaml"
        path.write_text(yaml.safe_dump(candidate, sort_keys=False), encoding="utf-8")
        url = f"http://ai-probe.invalid:{server.server_port}/probe"
        diagnostics: dict = {}

        qualified = probe_ai_nodes(
            _binary(),
            path,
            (
                {
                    "name": "local",
                    "url": url,
                    "method": "HEAD",
                    "expected_status": "204",
                    "timeout": 2000,
                },
            ),
            workers=1,
            diagnostics=diagnostics,
        )
        assert qualified == {"AI Direct"}
        assert observed == ["HEAD"]
        assert diagnostics == {
            "tested_nodes": 1,
            "qualified_nodes": 1,
            "selector_failures": 0,
            "probes": {
                "local": {
                    "method": "HEAD",
                    "expected_status": "204",
                    "passed": 1,
                    "failed": 0,
                    "outcomes": {"status_204": 1},
                }
            },
        }

        observed.clear()
        rejected_diagnostics: dict = {}
        rejected = probe_ai_nodes(
            _binary(),
            path,
            (
                {
                    "name": "local",
                    "url": url,
                    "method": "HEAD",
                    "expected_status": "200",
                    "timeout": 2000,
                },
            ),
            workers=1,
            diagnostics=rejected_diagnostics,
        )
        assert rejected == set()
        assert observed == ["HEAD"]
        assert rejected_diagnostics["qualified_nodes"] == 0
        assert rejected_diagnostics["probes"]["local"]["failed"] == 1
        assert rejected_diagnostics["probes"]["local"]["outcomes"] == {"status_204": 1}
    finally:
        server.shutdown()
        server.server_close()
