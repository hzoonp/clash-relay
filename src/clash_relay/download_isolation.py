"""Fail-closed declaration and rule-order contract for download isolation."""

from __future__ import annotations

from typing import Any

from .errors import ConfigurationError, ValidationError
from .policy_contract import load_policy_contract
from .routing_policy_v2 import load_routing_policy_v2
from .runtime_config_renderer import RuntimeConfigRenderer
from .web_general_runtime import WEB_GENERAL_AUTO_GROUP

_PROCESS_IDS = frozenset(
    {
        "download_android_play",
        "download_aria2",
        "download_aria2_windows",
        "download_idm",
        "download_fdm",
        "download_qbittorrent",
        "download_qbittorrent_unix",
        "download_transmission",
        "download_thunder",
    }
)
_DOMAIN_IDS = frozenset(
    {"download_play_gvt1", "download_play_gvt2", "download_github_release_assets"}
)
_INLINE_IDS = _PROCESS_IDS | _DOMAIN_IDS | {"download_inbound"}
_EXPECTED_INLINE = {
    "download_inbound": ("IN-NAME", "download-in", 1),
    "download_android_play": ("PROCESS-NAME", "com.android.vending", 2),
    "download_aria2": ("PROCESS-NAME", "aria2c", 2),
    "download_aria2_windows": ("PROCESS-NAME", "aria2c.exe", 2),
    "download_idm": ("PROCESS-NAME", "IDMan.exe", 2),
    "download_fdm": ("PROCESS-NAME", "fdm.exe", 2),
    "download_qbittorrent": ("PROCESS-NAME", "qbittorrent.exe", 2),
    "download_qbittorrent_unix": ("PROCESS-NAME", "qbittorrent", 2),
    "download_transmission": ("PROCESS-NAME", "transmission-gtk", 2),
    "download_thunder": ("PROCESS-NAME", "Thunder.exe", 2),
    "download_play_gvt1": ("DOMAIN-SUFFIX", "gvt1.com", 101),
    "download_play_gvt2": ("DOMAIN-SUFFIX", "gvt2.com", 101),
    "download_github_release_assets": (
        "DOMAIN",
        "release-assets.githubusercontent.com",
        102,
    ),
}


def _canonical(project: Any) -> bool:
    return any(spec.id == "subscription_1" and spec.enabled for spec in project.subscriptions)


def audit_download_declarations(project: Any) -> dict[str, Any]:
    """Check the public contract without claiming runtime graph evidence."""

    if not _canonical(project):
        return {"status": "not_applicable"}
    subscription_1 = next(spec for spec in project.subscriptions if spec.id == "subscription_1")
    if subscription_1.allowed_uses != frozenset({"browsing", "ai"}):
        raise ConfigurationError("subscription_1 must allow exactly browsing and ai")
    policy = load_routing_policy_v2(project.policies)
    if policy.scenario_use("download") != "general":
        raise ConfigurationError("download scenario must use general inventory")
    manifest = project.acl4ssr
    if not isinstance(manifest, dict):
        raise ConfigurationError("download isolation requires ACL4SSR routing")
    contract = load_policy_contract(project.policies)
    groups = {str(row["display_name"]): row for row in manifest.get("groups", [])}
    automatic = groups.get(contract.automatic_group("download"))
    if not isinstance(automatic, dict) or automatic.get("provider_pool") != "general":
        raise ConfigurationError("download automatic group must use the general pool")
    selector = groups.get(contract.public_group("download"))
    if not isinstance(selector, dict) or not selector.get("members"):
        raise ConfigurationError("download selector is missing")
    if selector["members"][0] != {"group": contract.automatic_group("download")}:
        raise ConfigurationError("download selector must default to download automatic group")

    sources = {str(row["id"]): row for row in manifest.get("sources", [])}
    download = sources.get("download")
    if (
        not isinstance(download, dict)
        or download.get("path") != "Clash/Download.list"
        or download.get("target") != contract.public_group("download")
        or download.get("source_use") != "general"
    ):
        raise ConfigurationError("ACL4SSR download source must target general-only download")
    proxy_lite = sources.get("proxy_lite")
    if (
        not isinstance(proxy_lite, dict)
        or proxy_lite.get("target") != WEB_GENERAL_AUTO_GROUP
        or proxy_lite.get("source_use") != "general"
        or proxy_lite.get("scenario") != "general"
    ):
        raise ConfigurationError("generic ProxyLite must use general inventory")
    web_auto = groups.get(WEB_GENERAL_AUTO_GROUP)
    general_pool = next(
        (row for row in project.policies["pools"] if row.get("id") == "general"),
        None,
    )
    browsing_pool = next(
        (row for row in project.policies["pools"] if row.get("id") == "browsing"),
        None,
    )
    web_route = web_auto.get("route") if isinstance(web_auto, dict) else None
    if (
        not isinstance(web_auto, dict)
        or web_auto.get("hidden") is not True
        or web_auto.get("type") != "url-test"
        or web_auto.get("prebuilt_members") is not True
        or "provider_pool" in web_auto
        or not isinstance(web_route, dict)
        or web_route.get("scenario") != "general"
        or web_route.get("profile") != "browsing"
        or not isinstance(general_pool, dict)
        or general_pool.get("source_use") != "general"
        or not isinstance(browsing_pool, dict)
        or browsing_pool.get("probe") != "browsing"
    ):
        raise ConfigurationError("generic ProxyLite requires direct general-only web URLTest")
    inline = {str(row["id"]): row for row in manifest.get("inline_rules", [])}
    if not inline.keys() >= _INLINE_IDS:
        raise ConfigurationError("download isolation inline rules are incomplete")
    for source_id in _INLINE_IDS:
        row = inline[source_id]
        if (
            row.get("target") != contract.public_group("download")
            or row.get("source_use") != "general"
            or row.get("scenario") != "download"
        ):
            raise ConfigurationError(f"download rule {source_id!r} bypasses general inventory")
        if source_id in _PROCESS_IDS and row.get("type") != "PROCESS-NAME":
            raise ConfigurationError(f"download process rule {source_id!r} changed type")
        if source_id in _DOMAIN_IDS and row.get("type") not in {"DOMAIN", "DOMAIN-SUFFIX"}:
            raise ConfigurationError(f"download domain rule {source_id!r} changed type")
        expected_type, expected_value, expected_priority = _EXPECTED_INLINE[source_id]
        if (
            row.get("type") != expected_type
            or row.get("value") != expected_value
            or row.get("priority") != expected_priority
        ):
            raise ConfigurationError(f"download rule {source_id!r} drifted from its review")
    if (
        inline["download_inbound"].get("type") != "IN-NAME"
        or inline["download_inbound"].get("value") != "download-in"
    ):
        raise ConfigurationError("download inbound rule must match download-in")
    if int(inline["download_inbound"]["priority"]) >= min(
        int(inline[source_id]["priority"]) for source_id in _PROCESS_IDS
    ):
        raise ConfigurationError("download inbound must precede download processes")
    if max(int(inline[source_id]["priority"]) for source_id in _PROCESS_IDS) >= min(
        int(row["priority"]) for row in sources.values()
    ):
        raise ConfigurationError("download processes must precede baseline domain rules")
    ai_priority = min(int(sources[key]["priority"]) for key in ("ai", "openai"))
    if max(int(inline[source_id]["priority"]) for source_id in _DOMAIN_IDS) >= ai_priority:
        raise ConfigurationError("download domains must precede AI rules")
    if int(download["priority"]) >= ai_priority:
        raise ConfigurationError("ACL4SSR Download must precede AI rules")

    inbound = project.config["runtime"].get("download_inbound")
    if not isinstance(inbound, dict) or inbound.get("enabled") is not True:
        raise ConfigurationError("download isolation requires an enabled download inbound")
    if project.config["runtime"]["mode"] != "rule":
        raise ConfigurationError("download inbound requires rule mode")
    if inbound["port"] == project.config["runtime"]["mixed_port"]:
        raise ConfigurationError("download inbound port must differ from mixed port")
    expected_listener = {
        "name": "download-in",
        "type": "mixed",
        "listen": "127.0.0.1",
        "port": inbound["port"],
        "proxy": contract.public_group("download"),
    }
    if RuntimeConfigRenderer().render(project.config).get("listeners") != [expected_listener]:
        raise ConfigurationError("download listener must bind directly to download selector")
    return {
        "status": "passed",
        "source_use": "general",
        "classifiers_declared": len(_INLINE_IDS) + 1,
        "inbound_enabled": bool(inbound and inbound.get("enabled")),
        "listener_binding_declared": True,
    }


def audit_download_rule_order(project: Any, candidate: dict[str, Any]) -> dict[str, Any]:
    """Audit emitted Mihomo directives after qualification transforms."""

    declaration = audit_download_declarations(project)
    if declaration["status"] == "not_applicable":
        return declaration
    if candidate.get("mode") != "rule":
        raise ValidationError("download classifier isolation requires rule mode")
    rules = candidate.get("rules")
    if not isinstance(rules, list) or not all(isinstance(rule, str) for rule in rules):
        raise ValidationError("download isolation requires emitted routing rules")
    manifest = project.acl4ssr
    assert isinstance(manifest, dict)
    inline = {str(row["id"]): row for row in manifest["inline_rules"]}
    target = load_policy_contract(project.policies).public_group("download")

    def rule_for(source_id: str) -> str:
        row = inline[source_id]
        return f"{row['type']},{row['value']},{target}"

    expected_inbound = rule_for("download_inbound")
    if not rules or rules[0] != expected_inbound or rules.count(expected_inbound) != 1:
        raise ValidationError("download inbound is not the first emitted rule")
    process_positions = []
    for source_id in _PROCESS_IDS:
        expected = rule_for(source_id)
        if rules.count(expected) != 1:
            raise ValidationError(f"download process rule {source_id!r} is missing or duplicated")
        process_positions.append(rules.index(expected))
    if sorted(process_positions) != list(range(2, 2 * len(_PROCESS_IDS) + 2, 2)):
        raise ValidationError("download processes must precede every domain rule")

    download_rule = f"RULE-SET,acl4ssr_download,{target}"
    if rules.count(download_rule) != 1:
        raise ValidationError("ACL4SSR Download rule is missing or duplicated")
    ai_positions = [
        index
        for index, rule in enumerate(rules)
        if rule.startswith("RULE-SET,acl4ssr_ai,")
        or rule.startswith("RULE-SET,acl4ssr_openai,")
        or rule.startswith("RULE-SET,cr_ai_rules_")
        or rule.startswith("RULE-SET,cr_openai_app,")
    ]
    if not ai_positions:
        raise ValidationError("download isolation requires AI routing rules")
    first_ai = min(ai_positions)
    for source_id in _DOMAIN_IDS:
        expected = rule_for(source_id)
        if rules.count(expected) != 1 or rules.index(expected) >= first_ai:
            raise ValidationError(f"download domain rule {source_id!r} must precede AI")
    if rules.index(download_rule) >= first_ai:
        raise ValidationError("ACL4SSR Download must precede AI")
    media_rule = "RULE-SET,acl4ssr_proxy_media,流媒体"
    generic_rule = f"RULE-SET,acl4ssr_proxy_lite,{WEB_GENERAL_AUTO_GROUP}"
    if rules.count(media_rule) != 1 or rules.count(generic_rule) != 1:
        raise ValidationError("media or generic routing rule is missing or duplicated")
    if not first_ai < rules.index(media_rule) < rules.index(generic_rule):
        raise ValidationError("AI, media, and generic routing order drifted")

    # Every classified download is terminal, including unsupported-UDP retries.
    # One declaration emits a primary rule and its adjacent REJECT guard.
    download_rows = [
        *(
            (str(row["id"]), rule_for(str(row["id"])))
            for row in manifest["inline_rules"]
            if row.get("scenario") == "download"
        ),
        *(
            (str(row["id"]), f"RULE-SET,acl4ssr_{row['id']},{target}")
            for row in manifest["sources"]
            if row.get("scenario") == "download"
        ),
    ]
    for source_id, primary in download_rows:
        guard = primary.rsplit(",", 1)[0] + ",REJECT"
        if rules.count(primary) != 1 or rules.count(guard) != 1:
            raise ValidationError(f"download terminal guard {source_id!r} is missing or duplicated")
        if rules.index(guard) != rules.index(primary) + 1:
            raise ValidationError(f"download terminal guard {source_id!r} is not adjacent")

    inbound = project.config["runtime"].get("download_inbound")
    if inbound and inbound.get("enabled") is True:
        listeners = candidate.get("listeners")
        expected_listener = {
            "name": "download-in",
            "type": "mixed",
            "listen": "127.0.0.1",
            "port": inbound["port"],
            "proxy": target,
        }
        if not isinstance(listeners, list) or listeners.count(expected_listener) != 1:
            raise ValidationError("download-only listener is missing or changed")
    return {
        "status": "passed",
        "inbound_rule_first": True,
        "process_rules": len(_PROCESS_IDS),
        "domain_rules": len(_DOMAIN_IDS),
        "acl4ssr_download_before_ai": True,
        "inbound_configured": bool(inbound and inbound.get("enabled")),
        "download_listener_bound": bool(inbound and inbound.get("enabled")),
        "terminal_guards": "passed",
        "terminal_guards_checked": len(download_rows),
    }
