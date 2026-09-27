from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from clash_relay.dns_compatibility import audit_fake_ip_compatibility
from clash_relay.errors import ValidationError


def _candidate(entries: list[str]) -> dict:
    return {
        "dns": {
            "enable": True,
            "enhanced-mode": "fake-ip",
            "fake-ip-filter": entries,
        }
    }


def test_fake_ip_compatibility_accepts_narrow_canonical_exceptions(repo_root: Path) -> None:
    config = yaml.safe_load((repo_root / "config.yaml").read_text(encoding="utf-8"))
    entries = config["runtime"]["dns"]["fake_ip_filter"]

    assert entries == [
        "*.lan",
        "*.local",
        "localhost",
        "*.cmpassport.com",
        "*.cmbchina.com.cn",
        "*.icbc.com.cn",
        "*.pingan.com.cn",
    ]
    assert audit_fake_ip_compatibility(_candidate(entries)) == {
        "status": "passed",
        "mode": "fake_ip",
        "entries": 7,
        "compatibility_entries": 4,
    }


@pytest.mark.parametrize("entry", ["*", "*.com", "*.cn", "*.com.cn", "example"])
def test_fake_ip_compatibility_rejects_overbroad_exclusions(entry: str) -> None:
    with pytest.raises(ValidationError, match=r"broader|wildcard"):
        audit_fake_ip_compatibility(_candidate(["*.lan", entry]))


@pytest.mark.parametrize(
    "entry",
    [
        "*.openai.com",
        "*.chatgpt.com",
        "*.anthropic.com",
        "*.claude.ai",
        "*.google.com",
        "*.googleapis.com",
        "gemini.google.com",
    ],
)
def test_fake_ip_compatibility_rejects_ai_overlap(entry: str) -> None:
    with pytest.raises(ValidationError, match="protected AI"):
        audit_fake_ip_compatibility(_candidate(["*.lan", entry]))


def test_fake_ip_compatibility_rejects_invalid_filter_shape() -> None:
    candidate = _candidate(["*.lan"])
    candidate["dns"]["fake-ip-filter"] = ["*.lan", 53]

    with pytest.raises(ValidationError, match="string list"):
        audit_fake_ip_compatibility(candidate)


def test_fake_ip_compatibility_is_not_applicable_without_fake_ip() -> None:
    assert audit_fake_ip_compatibility({}) == {
        "status": "not_applicable",
        "mode": "no_managed_dns",
    }
    candidate = _candidate(["*.lan"])
    candidate["dns"]["enhanced-mode"] = "redir-host"
    assert audit_fake_ip_compatibility(candidate) == {
        "status": "not_applicable",
        "mode": "not_fake_ip",
    }
