from __future__ import annotations

from pathlib import Path

import yaml

from clash_relay.openai_app_contract import (
    OPENAI_SERVICE_TARGET,
    RULE_PROVIDER,
    apply_route_lock,
    audit_route_lock,
)
from clash_relay.runtime_config_renderer import RuntimeConfigRenderer


def test_canonical_client_dns_sniffer_and_openai_route_lock_coexist(repo_root: Path) -> None:
    canonical = yaml.safe_load((repo_root / "config.yaml").read_text(encoding="utf-8"))
    runtime = RuntimeConfigRenderer().render(canonical)

    assert canonical["runtime"]["dns"] == {"mode": "client"}
    assert "dns" not in runtime
    assert runtime["sniffer"]["enable"] is True
    assert 443 in runtime["sniffer"]["sniff"]["TLS"]["ports"]
    assert 443 in runtime["sniffer"]["sniff"]["QUIC"]["ports"]
    assert runtime["sniffer"]["force-dns-mapping"] is False
    assert runtime["sniffer"]["parse-pure-ip"] is True

    candidate = {
        **runtime,
        "proxy-groups": [
            {
                "name": OPENAI_SERVICE_TARGET,
                "type": "select",
                "hidden": True,
                "proxies": ["REJECT"],
            }
        ],
        "rule-providers": {
            "acl4ssr_openai": {
                "type": "inline",
                "behavior": "classical",
                "payload": ["DOMAIN-SUFFIX,chatgpt.com"],
            },
            "acl4ssr_ai": {
                "type": "inline",
                "behavior": "classical",
                "payload": ["DOMAIN-SUFFIX,perplexity.ai"],
            },
        },
        "rules": [
            f"RULE-SET,acl4ssr_openai,{OPENAI_SERVICE_TARGET}",
            "RULE-SET,acl4ssr_ai,人工智能",
            "MATCH,人工智能",
        ],
    }

    apply_route_lock(candidate)

    assert candidate["rules"][0] == f"RULE-SET,{RULE_PROVIDER},{OPENAI_SERVICE_TARGET}"
    assert audit_route_lock(candidate)["status"] == "passed"
    assert "dns" not in candidate
    assert candidate["sniffer"] == runtime["sniffer"]
