from __future__ import annotations

from pathlib import Path

import yaml

import clash_relay.diagnose as diagnose_module
from clash_relay.diagnose import diagnose_candidate
from clash_relay.errors import ValidationError


def _candidate() -> dict:
    return {
        "mixed-port": 7890,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "warning",
        "dns": {
            "enable": True,
            "enhanced-mode": "fake-ip",
            "fake-ip-range": "198.18.0.1/16",
            "fake-ip-filter": ["*.lan", "*.local", "localhost", "*.cmpassport.com"],
            "default-nameserver": ["223.5.5.5"],
            "nameserver": ["https://dns.alidns.com/dns-query"],
            "proxy-server-nameserver": ["https://dns.alidns.com/dns-query"],
        },
        "proxy-providers": {},
        "proxy-groups": [
            {"name": name, "type": "select", "proxies": ["DIRECT"]}
            for name in (
                "代理选择",
                "网页浏览",
                "人工智能",
                "流媒体",
                "消息通讯",
                "下载流量",
            )
        ],
        "rules": ["MATCH,代理选择"],
    }


def test_diagnose_reports_only_aggregate_safe_results(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "candidate.yaml"
    path.write_text(yaml.safe_dump(_candidate(), allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(diagnose_module, "validate_generated_config", lambda candidate: None)

    report = diagnose_candidate(path)

    assert report["status"] == "passed"
    assert report["summary"] == {
        "status": "passed",
        "tested": 3,
        "passed": 3,
        "failed": 0,
        "skipped": 1,
    }
    assert report["checks"]["public_scenario_groups"]["present"] == 6
    serialized = str(report)
    assert "cmpassport.com" not in serialized
    assert "server" not in serialized


def test_diagnose_fails_closed_without_echoing_validator_details(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "candidate.yaml"
    path.write_text(yaml.safe_dump(_candidate(), allow_unicode=True), encoding="utf-8")

    def reject(candidate):
        raise ValidationError("PRIVATE proxy-name 10.0.0.8 secret.example")

    monkeypatch.setattr(diagnose_module, "validate_generated_config", reject)
    report = diagnose_candidate(path)
    serialized = str(report)

    assert report["status"] == "failed"
    assert report["summary"]["failed"] == 1
    assert "PRIVATE" not in serialized
    assert "10.0.0.8" not in serialized
    assert "secret.example" not in serialized


def test_diagnose_runtime_is_optional_and_aggregate(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "candidate.yaml"
    path.write_text(yaml.safe_dump(_candidate(), allow_unicode=True), encoding="utf-8")
    binary = tmp_path / "mihomo"
    binary.write_text("mock", encoding="utf-8")

    monkeypatch.setattr(diagnose_module, "validate_generated_config", lambda candidate: None)
    monkeypatch.setattr(
        diagnose_module,
        "validate_with_mihomo",
        lambda *args, **kwargs: {
            "binary": "mihomo",
            "version": "Mihomo Meta v1.test",
            "config_test": "passed",
            "startup_smoke": "passed",
            "startup_tun_disabled": True,
        },
    )

    report = diagnose_candidate(path, mihomo_bin=binary)

    assert report["status"] == "passed"
    assert report["summary"]["tested"] == 4
    assert report["summary"]["skipped"] == 0
    assert report["checks"]["mihomo_runtime"]["startup_smoke"] == "passed"
