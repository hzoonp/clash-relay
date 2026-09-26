from __future__ import annotations

import traceback
from pathlib import Path

import pytest

from clash_relay import final_link_smoke as smoke
from clash_relay.errors import PublicationError

URL = "https://entry.example.invalid/private-token" + "?key=" + "fixture-secret"
CONTENT = b"mixed-port: 7890\nmode: rule\nrules: [MATCH,DIRECT]\n"


def _verifier(tmp_path: Path, monkeypatch, *, content=CONTENT):
    binary = tmp_path / "mihomo"
    binary.write_bytes(b"fixture")
    monkeypatch.setattr(smoke, "_PROPAGATION_DELAYS", (0, 0, 0))
    return smoke.prepare_final_link_smoke(
        env={"CLASH_RELAY_PROFILE_URL": URL}, binary=binary, content=content
    )


@pytest.mark.parametrize(
    "url", ["", "http://entry.test/token", "https://127.0.0.1/token", URL + "#x"]
)
def test_invalid_or_missing_entry_fails_before_fetch(tmp_path, monkeypatch, url):
    monkeypatch.setattr(smoke, "fetch_https_bytes", lambda *a, **kw: pytest.fail("fetch"))
    with pytest.raises(PublicationError) as caught:
        smoke.prepare_final_link_smoke(
            env={"CLASH_RELAY_PROFILE_URL": url}, binary=tmp_path / "core", content=CONTENT
        )
    assert "private-token" not in str(caught.value)


def test_missing_core_is_prepublication_failure(tmp_path):
    with pytest.raises(PublicationError, match="validated Mihomo"):
        smoke.prepare_final_link_smoke(
            env={"CLASH_RELAY_PROFILE_URL": URL}, binary=None, content=CONTENT
        )


def test_stale_edge_then_exact_bytes_are_validated_and_cleaned(tmp_path, monkeypatch):
    responses = iter([b"old release", CONTENT])
    paths = []
    monkeypatch.setattr(smoke, "fetch_https_bytes", lambda *a, **kw: next(responses))

    def core(binary, path):
        assert path.read_bytes() == CONTENT
        paths.append(path)
        return {"config_test": "passed"}

    monkeypatch.setattr(smoke, "validate_with_mihomo", core)
    result = _verifier(tmp_path, monkeypatch)()
    assert result["attempts"] == 2
    assert result["digest"] == "matched"
    assert paths and not paths[0].exists()
    assert "entry.example" not in str(result)


@pytest.mark.parametrize(
    "response", [b"old", CONTENT.replace(b"\n", b"\r\n"), b"\xef\xbb\xbf" + CONTENT]
)
def test_digest_is_exact_and_mismatching_bytes_never_reach_core(tmp_path, monkeypatch, response):
    monkeypatch.setattr(smoke, "fetch_https_bytes", lambda *a, **kw: response)
    monkeypatch.setattr(smoke, "load_candidate", lambda *a: pytest.fail("parse"))
    with pytest.raises(PublicationError, match="digest_mismatch"):
        _verifier(tmp_path, monkeypatch)()


@pytest.mark.parametrize("stage", ["fetch", "core", "yaml"])
def test_failures_never_expose_url_nodes_credentials_or_parser_output(tmp_path, monkeypatch, stage):
    def fail(*a, **kw):
        raise ValueError(f"{URL} secret-node password=private-password")

    monkeypatch.setattr(
        smoke, "fetch_https_bytes", fail if stage == "fetch" else lambda *a, **kw: CONTENT
    )
    monkeypatch.setattr(smoke, "validate_with_mihomo", fail if stage == "core" else lambda *a: {})
    if stage == "yaml":
        monkeypatch.setattr(smoke, "load_candidate", fail)
    with pytest.raises(PublicationError) as caught:
        _verifier(tmp_path, monkeypatch)()
    output = "".join(traceback.format_exception(caught.value))
    for secret in (URL, "secret-node", "private-password"):
        assert secret not in output


def test_exact_invalid_yaml_is_rejected(tmp_path, monkeypatch):
    content = b"[broken"
    monkeypatch.setattr(smoke, "fetch_https_bytes", lambda *a, **kw: content)
    with pytest.raises(PublicationError, match="yaml_or_mihomo"):
        _verifier(tmp_path, monkeypatch, content=content)()
