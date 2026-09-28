from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from clash_relay.subscription_parser import parse_subscription
from clash_relay.util import dump_yaml

pytestmark = pytest.mark.integration
FIXTURES = Path(__file__).parents[1] / "fixtures" / "uri"


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.yaml")), ids=lambda path: path.stem)
def test_uri_semantics_load_in_pinned_mihomo(path: Path, tmp_path: Path) -> None:
    binary = os.environ.get("MIHOMO_BIN")
    if not binary:
        pytest.skip("MIHOMO_BIN is not set")
    fixture = yaml.safe_load(path.read_text(encoding="utf-8"))
    proxy = parse_subscription(fixture["uri"]).proxies[0]
    config = {
        "mixed-port": 17890,
        "allow-lan": False,
        "mode": "rule",
        "proxies": [proxy],
        "proxy-groups": [{"name": "Fixture", "type": "select", "proxies": [proxy["name"]]}],
        "rules": ["MATCH,Fixture"],
    }
    target = tmp_path / "config.yaml"
    target.write_text(dump_yaml(config), encoding="utf-8")

    process = subprocess.run(
        [binary, "-t", "-d", str(tmp_path), "-f", str(target)],
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )
    assert process.returncode == 0, f"Mihomo rejected {path.stem} URI fixture"
