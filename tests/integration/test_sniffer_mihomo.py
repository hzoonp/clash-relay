from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from clash_relay.builder import build_candidate
from clash_relay.mihomo import validate_with_mihomo

pytestmark = pytest.mark.integration


def _binary() -> Path:
    value = os.environ.get("MIHOMO_BIN")
    if not value:
        pytest.skip("MIHOMO_BIN is not set")
    return Path(value).resolve()


def _sniffer() -> dict[str, object]:
    return {
        "enabled": True,
        "force_dns_mapping": False,
        "parse_pure_ip": True,
        "sniff": {
            "http": {"ports": [80, "8080-8880"], "override_destination": True},
            "tls": {"ports": [443, 8443]},
            "quic": {"ports": [443, 8443]},
        },
    }


def test_client_owned_dns_with_http_tls_quic_sniffer_loads_and_starts(
    project_factory, yaml_editor, fixture_env, tmp_path: Path
) -> None:
    _, paths = project_factory()

    def mutate(data: dict) -> None:
        data["runtime"]["dns"] = {"mode": "client"}
        data["runtime"]["profile"]["store_fake_ip"] = False
        data["runtime"]["sniffer"] = _sniffer()

    yaml_editor(paths["config_path"], mutate)
    result = build_candidate(**paths, env=fixture_env)
    rendered = yaml.safe_load(result.yaml_text)

    assert "dns" not in rendered
    assert "store-fake-ip" not in rendered["profile"]
    assert rendered["sniffer"] == {
        "enable": True,
        "force-dns-mapping": False,
        "parse-pure-ip": True,
        "sniff": {
            "HTTP": {"ports": [80, "8080-8880"], "override-destination": True},
            "TLS": {"ports": [443, 8443]},
            "QUIC": {"ports": [443, 8443]},
        },
    }

    candidate = tmp_path / "sniffer-candidate.yaml"
    candidate.write_text(result.yaml_text, encoding="utf-8")
    report = validate_with_mihomo(_binary(), candidate, startup_seconds=1.0)
    assert report["config_test"] == "passed"
    assert report["startup_smoke"] == "passed"
