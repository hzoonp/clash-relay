from __future__ import annotations

import json
from pathlib import Path

from clash_relay import cli
from clash_relay.config_loader import load_project
from clash_relay.doctor import run_doctor
from clash_relay.effective_config import describe_effective_config


def _paths(root: Path) -> dict[str, Path]:
    return {
        "config_path": root / "config.yaml",
        "subscriptions_path": root / "subscriptions.yaml",
        "policies_path": root / "policies.yaml",
    }


def test_profile_compilation_preserves_declared_dns_and_explains_effective_values(
    repo_root: Path,
) -> None:
    path = repo_root / "config.yaml"
    original = path.read_bytes()
    project = load_project(**_paths(repo_root))
    report = describe_effective_config(project)

    assert project.declared_config is not None
    assert project.declared_config["runtime"]["dns"]["proxy_server_nameservers"] == [
        "https://1.1.1.1/dns-query",
        "https://8.8.8.8/dns-query",
        "https://223.5.5.5/dns-query",
    ]
    assert project.config["runtime"]["dns"]["proxy_server_nameservers"] == [
        "system",
        "https://dns.alidns.com/dns-query",
        "https://doh.pub/dns-query",
    ]
    assert path.read_bytes() == original
    assert report["scope"] == "public_declarations"
    assert report["network_profile"] == "cn_three_net"
    assert report["effective"]["urltest"]["regional"]["interval"] == 300
    assert report["effective"]["urltest"]["regional"]["tolerance_ms"]["US"] == 150
    assert {row["field"] for row in report["overrides"]} == {"runtime.dns.proxy_server_nameservers"}
    assert "SUBSCRIPTION_1_URL" not in repr(report)


def test_public_effective_config_and_doctor_report_the_same_override(
    repo_root: Path, capsys
) -> None:
    paths = _paths(repo_root)
    assert cli.main(["effective-config", "--config", str(paths["config_path"])]) == 0
    effective = json.loads(capsys.readouterr().out)
    doctor = run_doctor(**paths, public_only=True)

    assert doctor["public"]["network_profile"] == effective["network_profile"]
    assert doctor["public"]["network_profile_overrides"] == effective["overrides"]
