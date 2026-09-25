from __future__ import annotations

import copy

import pytest

import clash_relay.ai_qualification as ai_qualification
from clash_relay.ai_qualification import (
    _new_diagnostics,
    _record_region_results,
    apply_ai_qualification,
    load_ai_probe_specs,
)
from clash_relay.errors import ValidationError


def _provider(*names: str) -> dict:
    return {
        "type": "inline",
        "health-check": {
            "enable": True,
            "url": "https://www.gstatic.com/generate_204",
            "interval": 300,
            "timeout": 5000,
            "lazy": True,
            "expected-status": "204",
        },
        "payload": [
            {"name": name, "type": "http", "server": f"{index}.invalid.example", "port": 443}
            for index, name in enumerate(names, start=1)
        ],
    }


def _auto(name: str, provider: str) -> dict:
    return {
        "name": name,
        "type": "url-test",
        "hidden": True,
        "use": [provider],
        "url": "https://www.gstatic.com/generate_204",
        "interval": 300,
        "timeout": 5000,
        "lazy": True,
        "expected-status": "204",
        "tolerance": 50,
    }


def _config() -> dict:
    return {
        "proxy-providers": {
            "cr_general_any": _provider("general-only"),
            "cr_ai_sg_sg": _provider("sg-good", "sg-bad"),
            "cr_ai_us_us": _provider("us-bad"),
        },
        "proxy-groups": [
            _auto("__CR_AUTO_GENERAL_ANY", "cr_general_any"),
            {
                "name": "节点选择",
                "type": "select",
                "proxies": ["__CR_AUTO_GENERAL_ANY"],
                "use": ["cr_general_any"],
            },
            _auto("__CR_AUTO_AI_SG_SG", "cr_ai_sg_sg"),
            {
                "name": "AI · 新加坡",
                "type": "select",
                "proxies": ["__CR_AUTO_AI_SG_SG"],
                "use": ["cr_ai_sg_sg"],
            },
            _auto("__CR_AUTO_AI_US_US", "cr_ai_us_us"),
            {
                "name": "AI · 美国",
                "type": "select",
                "proxies": ["__CR_AUTO_AI_US_US"],
                "use": ["cr_ai_us_us"],
            },
            {
                "name": "__CR_FAIL_CLOSED_AI_KR",
                "type": "select",
                "hidden": True,
                "proxies": ["REJECT"],
            },
            {
                "name": "AI · 韩国",
                "type": "select",
                "proxies": ["__CR_FAIL_CLOSED_AI_KR"],
            },
            {
                "name": "人工智能",
                "type": "select",
                "proxies": ["AI · 新加坡", "AI · 美国", "AI · 韩国", "DIRECT"],
            },
        ],
    }


def test_ai_probe_loader_preserves_declared_head_method(tmp_path) -> None:
    policies = tmp_path / "policies.yaml"
    policies.write_text(
        """probes:
  ai_openai:
    url: https://chatgpt.com/
    method: HEAD
    expected_status: '200-399'
    timeout: 5000
""",
        encoding="utf-8",
    )

    specs = load_ai_probe_specs(policies, names=("ai_openai",))

    assert specs == (
        {
            "name": "ai_openai",
            "url": "https://chatgpt.com/",
            "method": "HEAD",
            "expected_status": "200-399",
            "timeout": 5000,
        },
    )


def test_ai_probe_loader_rejects_method_drift(tmp_path) -> None:
    policies = tmp_path / "policies.yaml"
    policies.write_text(
        """probes:
  ai_openai:
    url: https://chatgpt.com/
    method: GET
    expected_status: '200-399'
    timeout: 5000
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="must use HEAD"):
        load_ai_probe_specs(policies, names=("ai_openai",))


def test_ai_qualification_keeps_only_live_nodes_and_prunes_empty_countries() -> None:
    config = _config()
    report = apply_ai_qualification(config, {"sg-good"})

    assert [item["name"] for item in config["proxy-providers"]["cr_ai_sg_sg"]["payload"]] == [
        "sg-good"
    ]
    assert "cr_ai_us_us" not in config["proxy-providers"]
    assert config["proxy-providers"]["cr_general_any"]["payload"][0]["name"] == "general-only"

    groups = {item["name"]: item for item in config["proxy-groups"]}
    assert "AI · 新加坡" in groups
    assert "AI · 美国" not in groups
    assert "AI · 韩国" not in groups
    assert "__CR_AUTO_AI_US_US" not in groups
    assert "__CR_FAIL_CLOSED_AI_KR" not in groups
    assert groups["人工智能"]["proxies"] == ["AI · 新加坡", "DIRECT"]

    assert report == {
        "tested_nodes": 3,
        "qualified_nodes": 1,
        "country_groups": {"AI · 新加坡": 1, "AI · 美国": 0},
        "removed_country_groups": ["AI · 美国", "AI · 韩国"],
    }
    assert "sg-good" not in repr(report)


def test_ai_qualification_fails_closed_when_no_node_passes() -> None:
    config = copy.deepcopy(_config())
    with pytest.raises(ValidationError, match="no nodes passed all AI qualification probes"):
        apply_ai_qualification(config, set())


def test_ai_probe_detaches_production_dns_rule_set_policy() -> None:
    provider = _provider("ai-node")
    base = {
        "proxy-providers": {"cr_ai_us_us": provider},
        "dns": {
            "enable": True,
            "listen": "127.0.0.1:1053",
            "nameserver-policy": {"rule-set:acl4ssr_openai": ["https://1.1.1.1/dns-query"]},
        },
    }

    probe = ai_qualification._temporary_probe_config(
        base,
        provider_name="cr_ai_us_us",
        payload=(provider["payload"][0],),
        mixed_port=17891,
        controller_port=19091,
        secret="test",
    )

    assert "nameserver-policy" not in probe["dns"]


def test_record_region_results_writes_explicit_endpoint_evidence() -> None:
    """Explicit passed/failed/reached counters are judged by the probe's own
    expected_status: a status_200 against expected 204 is reached but failed."""

    diagnostics = _new_diagnostics(
        ({"name": "connectivity", "method": "HEAD", "expected_status": "204"},)
    )
    payload = ({"name": "node-a"}, {"name": "node-b"})
    node_results = {
        "node-a": ({"probe": "connectivity", "passed": False, "outcome": "status_200"},),
        "node-b": ({"probe": "connectivity", "passed": True, "outcome": "status_204"},),
    }

    _record_region_results(
        diagnostics,
        region="JP",
        payload=payload,
        qualified={"node-b"},
        node_results=node_results,
    )

    row = diagnostics["regions"]["JP"]
    assert row["tested"] == 2
    assert row["qualified"] == 1
    endpoint = row["endpoints"]["connectivity"]
    assert endpoint == {
        "probed": 2,
        "passed": 1,
        "failed": 1,
        "reached": 2,
        "network_failure": 0,
        "outcomes": {"status_200": 1, "status_204": 1},
    }


def test_record_region_results_counts_network_failures() -> None:
    diagnostics = _new_diagnostics(
        ({"name": "ai_openai", "method": "HEAD", "expected_status": "200-399"},)
    )

    _record_region_results(
        diagnostics,
        region="SG",
        payload=({"name": "node-c"},),
        qualified=set(),
        node_results={
            "node-c": ({"probe": "ai_openai", "passed": False, "outcome": "connection_error"},)
        },
    )

    endpoint = diagnostics["regions"]["SG"]["endpoints"]["ai_openai"]
    assert endpoint["reached"] == 0
    assert endpoint["network_failure"] == 1
    assert endpoint["passed"] == 0
    assert endpoint["failed"] == 1


def test_merge_diagnostics_preserves_endpoint_evidence_across_shards() -> None:
    """P0 regression: shard merge must aggregate region endpoint evidence
    (probed/passed/failed/reached/network_failure/outcomes) instead of
    dropping it."""

    from clash_relay.ai_qualification import _merge_diagnostics

    target = _new_diagnostics(
        ({"name": "ai_openai", "method": "HEAD", "expected_status": "200-399"},)
    )
    target["regions"]["JP"] = {
        "tested": 1,
        "qualified": 0,
        "endpoints": {
            "ai_openai": {
                "probed": 1,
                "passed": 0,
                "failed": 1,
                "reached": 0,
                "network_failure": 1,
                "outcomes": {"timeout": 1},
            }
        },
    }
    source = _new_diagnostics(
        ({"name": "ai_openai", "method": "HEAD", "expected_status": "200-399"},)
    )
    source["regions"]["JP"] = {
        "tested": 1,
        "qualified": 1,
        "endpoints": {
            "ai_openai": {
                "probed": 1,
                "passed": 1,
                "failed": 0,
                "reached": 1,
                "network_failure": 0,
                "outcomes": {"status_204": 1},
            }
        },
    }
    source["regions"]["SG"] = {
        "tested": 1,
        "qualified": 1,
        "endpoints": {
            "ai_openai": {
                "probed": 1,
                "passed": 1,
                "failed": 0,
                "reached": 1,
                "network_failure": 0,
                "outcomes": {"status_200": 1},
            }
        },
    }

    _merge_diagnostics(target, source)

    jp = target["regions"]["JP"]
    assert jp["tested"] == 2
    assert jp["qualified"] == 1
    assert jp["endpoints"]["ai_openai"]["probed"] == 2
    assert jp["endpoints"]["ai_openai"]["passed"] == 1
    assert jp["endpoints"]["ai_openai"]["failed"] == 1
    assert jp["endpoints"]["ai_openai"]["reached"] == 1
    assert jp["endpoints"]["ai_openai"]["network_failure"] == 1
    assert jp["endpoints"]["ai_openai"]["outcomes"] == {"timeout": 1, "status_204": 1}
    # A region present only in the merged shard is carried over whole.
    assert target["regions"]["SG"]["endpoints"]["ai_openai"]["reached"] == 1
