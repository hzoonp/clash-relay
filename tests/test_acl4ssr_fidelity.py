from __future__ import annotations

from pathlib import Path

import yaml

from clash_relay.acl4ssr_reference import (
    parse_acl4ssr_online,
    validate_acl4ssr_fidelity,
)


def _canonical(repo_root: Path) -> tuple[dict, str]:
    manifest = yaml.safe_load(
        (repo_root / "rules/acl4ssr.yaml").read_text(encoding="utf-8")
    )
    reference = (repo_root / "rules/acl4ssr-online.reference.ini").read_text(
        encoding="utf-8"
    )
    return manifest, reference


def test_pinned_acl4ssr_online_reference_is_parsed_as_the_baseline(
    repo_root: Path,
) -> None:
    manifest, reference = _canonical(repo_root)
    parsed = parse_acl4ssr_online(reference, repository=manifest["repository"])

    source_paths = [
        row["path"] for row in parsed["rulesets"] if row["kind"] == "source"
    ]
    assert source_paths == [
        "Clash/LocalAreaNetwork.list",
        "Clash/UnBan.list",
        "Clash/BanAD.list",
        "Clash/BanProgramAD.list",
        "Clash/Ruleset/GoogleFCM.list",
        "Clash/GoogleCN.list",
        "Clash/Ruleset/SteamCN.list",
        "Clash/Microsoft.list",
        "Clash/Apple.list",
        "Clash/Telegram.list",
        "Clash/ProxyMedia.list",
        "Clash/ProxyLite.list",
        "Clash/ChinaDomain.list",
        "Clash/ChinaCompanyIp.list",
    ]
    assert [row for row in parsed["rulesets"] if row["kind"] == "inline"] == [
        {
            "kind": "inline",
            "type": "GEOIP",
            "value": "CN",
            "target": "🎯 全球直连",
        }
    ]
    assert [row for row in parsed["rulesets"] if row["kind"] == "final"] == [
        {"kind": "final", "target": "🐟 漏网之鱼"}
    ]


def test_canonical_manifest_matches_reference_with_only_declared_deviations(
    repo_root: Path,
) -> None:
    manifest, reference = _canonical(repo_root)
    report = validate_acl4ssr_fidelity(manifest, reference_text=reference)

    assert report == {
        "status": "matched_with_declared_deviations",
        "reference_path": "Clash/config/ACL4SSR_Online.ini",
        "baseline_sources": 13,
        "compatibility_groups_checked": 7,
        "adapted_groups": 3,
        "disabled_sources": 1,
        "extensions": ["ai", "download", "openai"],
        "node_wildcards_omitted_for_source_isolation": True,
    }


def test_application_cleanup_is_the_only_disabled_acl4ssr_baseline_source(
    repo_root: Path,
) -> None:
    manifest, _reference = _canonical(repo_root)
    assert manifest["reference"]["disabled_paths"] == ["Clash/BanProgramAD.list"]
    groups = {row["display_name"] for row in manifest["groups"]}
    assert "应用净化" not in groups


def test_classification_extensions_are_only_ai_and_download(repo_root: Path) -> None:
    manifest, _reference = _canonical(repo_root)
    assert manifest["reference"]["extensions"] == [
        {"source_id": "ai", "before_path": "Clash/ProxyMedia.list"},
        {"source_id": "openai", "before_path": "Clash/ProxyMedia.list"},
        {"source_id": "download", "before_path": "Clash/ProxyLite.list"},
    ]
    sources = {row["id"]: row for row in manifest["sources"]}
    assert sources["proxy_media"]["target"] == "流媒体"
    assert sources["telegram"]["target"] == "消息通讯"
    assert sources["proxy_lite"]["target"] == "网页浏览"
    assert sources["download"]["target"] == "下载流量"
    assert "proxy_gfwlist" not in sources
