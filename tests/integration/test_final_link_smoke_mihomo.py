from __future__ import annotations

import os
import shutil
import socket
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from clash_relay import fetch, final_link_smoke
from clash_relay.errors import PublicationError

pytestmark = pytest.mark.integration


@pytest.fixture
def https_entry(tmp_path, monkeypatch):
    openssl = shutil.which("openssl")
    if not openssl:
        git_openssl = Path("C:/Program Files/Git/usr/bin/openssl.exe")
        if git_openssl.is_file():
            openssl = str(git_openssl)
    if not openssl:
        pytest.skip("OpenSSL is required for the local HTTPS fixture")
    cert, key = tmp_path / "fixture.crt", tmp_path / "fixture.key"
    result = subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-subj",
            "/CN=entry.example.invalid",
            "-addext",
            "subjectAltName=DNS:entry.example.invalid",
        ],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, "local TLS fixture certificate generation failed"
    content = (
        b"mixed-port: 0\nmode: rule\nlog-level: silent\n"
        b"proxies:\n  - {name: private-fixture-node, type: direct}\n"
        b"rules:\n  - MATCH,private-fixture-node\n"
    )
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/yaml")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_args):
            pass

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Only the resolver boundary is replaced. Production pinned connection,
    # HTTPS handshake, certificate/hostname verification and body fetch run.
    monkeypatch.setattr(
        fetch,
        "_resolve_public_destination",
        lambda *_args, **_kwargs: (
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("127.0.0.1", server.server_port),
            ),
        ),
    )
    monkeypatch.setattr(final_link_smoke, "_PROPAGATION_DELAYS", (0,))
    try:
        yield cert, server.server_port, content, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("tls_mode", ["trusted", "untrusted", "wrong_hostname"])
def test_final_entry_https_fetch_and_real_mihomo(https_entry, monkeypatch, capsys, tls_mode):
    value = os.environ.get("MIHOMO_BIN")
    if not value:
        pytest.skip("MIHOMO_BIN is not set")
    binary = Path(value).resolve()
    cert, port, content, requests = https_entry
    if tls_mode != "untrusted":
        trusted = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        trusted.load_verify_locations(cafile=str(cert))
        assert trusted.check_hostname and trusted.verify_mode == ssl.CERT_REQUIRED
        monkeypatch.setattr(fetch.ssl, "create_default_context", lambda: trusted)
    host = "wrong.example.invalid" if tls_mode == "wrong_hostname" else "entry.example.invalid"
    url = f"https://{host}:{port}/private-fixture-token" + "?key=" + "fixture-secret"
    verify = final_link_smoke.prepare_final_link_smoke(
        env={"CLASH_RELAY_PROFILE_URL": url},
        binary=binary,
        content=content,
    )
    if tls_mode == "trusted":
        assert verify() == {
            "status": "passed",
            "https": "passed",
            "yaml": "passed",
            "mihomo": "passed",
            "digest": "matched",
            "attempts": 1,
        }
        assert requests == ["/private-fixture-token?key=fixture-secret"]
    else:
        with pytest.raises(PublicationError, match="final-link smoke failed: https_fetch"):
            verify()
        assert requests == []
    output = capsys.readouterr()
    for secret in (url, "fixture-secret", "private-fixture-token", "private-fixture-node"):
        assert secret not in output.out + output.err
