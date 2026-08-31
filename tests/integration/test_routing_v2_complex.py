from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from clash_relay.ai_service_qualification import apply_ai_service_qualification
from clash_relay.builder import build_candidate
from clash_relay.config_loader import load_project
from clash_relay.mihomo import validate_with_mihomo
from clash_relay.routing_v2_audit import audit_routing_v2
from clash_relay.util import dump_yaml

pytestmark = pytest.mark.integration


def _runtime_names(candidate: dict, provider_name: str) -> set[str]:
    provider = candidate["proxy-providers"][provider_name]
    return {str(proxy["name"]) for proxy in provider["payload"]}


def test_complex_routing_v2_candidate_is_accepted_by_real_mihomo(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    secret_file = tmp_path / "subscriptions.yaml"
    secret_file.write_text(
        yaml.safe_dump(
            {
                f"SUBSCRIPTION_{index}_URL": f"https://fixture.invalid/subscription-{index}"
                for index in range(1, 5)
            },
            allow_unicode=True,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    def fake_subscription(url: str, **_kwargs) -> str:
        source_number = int(url.rsplit("-", 1)[1])
        base_port = 24000 + source_number * 100
        names = ["香港", "台湾", "新加坡", "日本", "美国", "韩国", "其他"]
        return yaml.safe_dump(
            {
                "proxies": [
                    {
                        "name": f"{name} Complex {source_number}",
                        "type": "http",
                        "server": "example.com",
                        "port": base_port + offset,
                    }
                    for offset, name in enumerate(names, start=1)
                ]
            },
            allow_unicode=True,
            sort_keys=False,
        )

    result = build_candidate(
        config_path=repo_root / "config.yaml",
        subscriptions_path=repo_root / "subscriptions.yaml",
        services_path=repo_root / "services.yaml",
        policies_path=repo_root / "policies.yaml",
        secret_file=secret_file,
        env={},
        fetcher=fake_subscription,
    )
    candidate = result.config

    assert "cr_ai_hk_hk" not in candidate["proxy-providers"]
    apply_ai_service_qualification(
        candidate,
        {
            "ai_openai": _runtime_names(candidate, "cr_ai_us_us"),
            "ai_claude": _runtime_names(candidate, "cr_ai_sg_sg"),
            "ai_gemini": _runtime_names(candidate, "cr_ai_jp_jp"),
        },
        preferred_regions=("US", "SG", "JP", "TW", "KR", "OTHER"),
    )

    groups = {row["name"]: row for row in candidate["proxy-groups"]}
    visible = {row["name"] for row in candidate["proxy-groups"] if not row.get("hidden", False)}
    assert visible == {
        "代理选择",
        "网页浏览",
        "人工智能",
        "流媒体",
        "消息通讯",
        "下载流量",
    }
    general_choices = [
        "香港节点",
        "台湾节点",
        "新加坡节点",
        "日本节点",
        "美国节点",
        "韩国节点",
        "DIRECT",
    ]
    assert groups["流媒体"]["proxies"] == ["媒体自动", *general_choices]
    assert groups["消息通讯"]["proxies"] == ["通讯自动", *general_choices]
    assert groups["下载流量"]["proxies"] == ["下载自动", *general_choices]
    for public_name in ("流媒体", "消息通讯", "下载流量"):
        assert "use" not in groups[public_name]
        assert "filter" not in groups[public_name]

    assert groups["电报消息"]["proxies"] == ["消息通讯"]
    assert groups["油管视频"]["proxies"] == ["流媒体"]
    assert groups["国外媒体"]["proxies"] == ["流媒体"]
    assert groups["国内媒体"]["proxies"] == ["DIRECT"]
    assert groups["漏网之鱼"]["proxies"] == ["代理选择"]
    assert groups["奈飞视频"]["type"] == "fallback"
    assert groups["奈飞视频"]["proxies"] == ["奈飞节点", "流媒体"]

    assert groups["__CR_AI_SERVICE_OPENAI"]["proxies"][0].startswith("__CR_AI_OPENAI_")
    assert groups["__CR_AI_SERVICE_CLAUDE"]["proxies"][0].startswith("__CR_AI_CLAUDE_")
    assert groups["__CR_AI_SERVICE_GEMINI"]["proxies"][0].startswith("__CR_AI_GEMINI_")
    assert sum(name.startswith("__CR_AI_OPENAI_") for name in groups) == 1
    assert sum(name.startswith("__CR_AI_CLAUDE_") for name in groups) == 1
    assert sum(name.startswith("__CR_AI_GEMINI_") for name in groups) == 1

    rules = candidate["rules"]
    assert "RULE-SET,acl4ssr_telegram,电报消息" in rules
    assert "RULE-SET,acl4ssr_youtube,油管视频" in rules
    assert "RULE-SET,acl4ssr_netflix,奈飞视频" in rules
    assert "RULE-SET,acl4ssr_download,下载流量" in rules
    assert "RULE-SET,acl4ssr_proxy_gfwlist,网页浏览" in rules
    assert "RULE-SET,acl4ssr_openai,__CR_AI_SERVICE_OPENAI" in rules
    assert "RULE-SET,cr_ai_rules_claude,__CR_AI_SERVICE_CLAUDE" in rules
    assert "RULE-SET,cr_ai_rules_gemini,__CR_AI_SERVICE_GEMINI" in rules
    assert rules[-1] == "MATCH,漏网之鱼"
    assert rules.index("RULE-SET,acl4ssr_china_domain,全球直连") < rules.index(
        "RULE-SET,acl4ssr_download,下载流量"
    )
    assert rules.index("RULE-SET,acl4ssr_download,下载流量") < rules.index(
        "RULE-SET,acl4ssr_proxy_gfwlist,网页浏览"
    )

    project = load_project(
        config_path=repo_root / "config.yaml",
        subscriptions_path=repo_root / "subscriptions.yaml",
        services_path=repo_root / "services.yaml",
        policies_path=repo_root / "policies.yaml",
    )
    audit = audit_routing_v2(project, candidate)
    assert audit["status"] == "passed"
    assert audit["cutover"]["download_mode"] == "general_auto"
    assert audit["cutover"]["messaging_scheduler"] == "通讯自动"
    assert audit["ai"]["stage"] == "post_qualification"

    path = tmp_path / "routing-v2-complex.yaml"
    path.write_text(dump_yaml(candidate), encoding="utf-8")
    validation = validate_with_mihomo(
        Path(os.environ["MIHOMO_BIN"]),
        path,
        secret_values=result.secret_values,
    )
    assert validation["config_test"] == "passed"
    assert validation["startup_smoke"] == "passed"
