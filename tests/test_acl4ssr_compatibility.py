from __future__ import annotations

import pytest

from clash_relay.acl4ssr import load_acl4ssr_rules
from clash_relay.errors import GenerationError


def _manifest(*, compatibility_path: str | None = None) -> dict:
    source = {
        "id": "fixture",
        "path": "Clash/Fixture.list",
        "target": "DIRECT",
        "priority": 10,
    }
    if compatibility_path is not None:
        source["mihomo_compatibility_path"] = compatibility_path
    return {
        "version": 1,
        "repository": "ACL4SSR/ACL4SSR",
        "ref": "0123456789abcdef0123456789abcdef01234567",
        "license": "CC-BY-SA-4.0",
        "max_source_bytes": 65536,
        "sources": [source],
        "inline_rules": [],
    }


def test_legacy_rule_is_omitted_only_with_exact_pinned_provider_evidence() -> None:
    def fetcher(url: str, **_kwargs) -> str:
        if url.endswith("/Clash/Fixture.list"):
            return "DOMAIN-SUFFIX,example.com\nURL-REGEX,^https?://example\\.com/path\n"
        if url.endswith("/Clash/Providers/Fixture.yaml"):
            return "payload:\n  - DOMAIN-SUFFIX,example.com\n  # URL-REGEX,^https?://example\\.com/path\n"
        raise AssertionError(url)

    providers, directives, report = load_acl4ssr_rules(
        _manifest(),
        modules={"general": True},
        fetcher=fetcher,
        timeout=5,
    )

    assert providers["acl4ssr_fixture"]["payload"] == ["DOMAIN-SUFFIX,example.com"]
    assert directives[0]["target"] == "DIRECT"
    assert report is not None
    assert report["verified_compatibility_omissions"] == 1
    assert report["unverified_legacy_rules"] == 0
    assert report["sources"][0]["mihomo_compatibility_path"] == (
        "Clash/Providers/Fixture.yaml"
    )


def test_legacy_rule_fails_closed_when_provider_does_not_document_exact_omission() -> None:
    def fetcher(url: str, **_kwargs) -> str:
        if url.endswith("/Clash/Fixture.list"):
            return "DOMAIN-SUFFIX,example.com\nURL-REGEX,^https?://example\\.com/path\n"
        if url.endswith("/Clash/Providers/Fixture.yaml"):
            return "payload:\n  - DOMAIN-SUFFIX,example.com\n"
        raise AssertionError(url)

    with pytest.raises(GenerationError, match="not explicitly omitted"):
        load_acl4ssr_rules(
            _manifest(),
            modules={"general": True},
            fetcher=fetcher,
            timeout=5,
        )


def test_explicit_compatibility_path_must_stay_under_acl4ssr_provider_tree() -> None:
    def fetcher(url: str, **_kwargs) -> str:
        if url.endswith("/Clash/Fixture.list"):
            return "DOMAIN-SUFFIX,example.com\nURL-REGEX,^https?://example\\.com/path\n"
        raise AssertionError(url)

    with pytest.raises(GenerationError, match="unsafe Mihomo compatibility provider path"):
        load_acl4ssr_rules(
            _manifest(compatibility_path="../private.yaml"),
            modules={"general": True},
            fetcher=fetcher,
            timeout=5,
        )
