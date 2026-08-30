from __future__ import annotations

import os
import threading
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
        url = f"http://127.0.0.1:{server.server_port}/probe"

        qualified = probe_ai_nodes(
            _binary(),
            path,
            ({"name": "local", "url": url, "expected_status": "204", "timeout": 2000},),
        )
        assert qualified == {"AI Direct"}
        assert ("HEAD" in observed) or ("GET" in observed)

        rejected = probe_ai_nodes(
            _binary(),
            path,
            ({"name": "local", "url": url, "expected_status": "200", "timeout": 2000},),
        )
        assert rejected == set()
    finally:
        server.shutdown()
        server.server_close()
