from __future__ import annotations

from pathlib import Path

import clash_relay.policy_compiler as compiler
from clash_relay.mihomo_serializer import serialize_runtime_graph


def test_policy_compiler_owns_all_pre_serialization_topology_passes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def fake_generate_config(**_kwargs):
        calls.append("draft")
        return (
            {
                "proxy-providers": {},
                "proxy-groups": [{"name": "base", "type": "select", "proxies": ["DIRECT"]}],
                "rules": ["MATCH,DIRECT"],
            },
            {"proxy_groups": 1},
        )

    def group_semantics(output, **_kwargs):
        calls.append("group_semantics")
        output["compiler-group-semantics"] = True
        return {"hidden_groups": []}

    def source_exclusions(output, **_kwargs):
        calls.append("source_exclusions")
        assert output["compiler-group-semantics"] is True
        output["compiler-source-exclusions"] = True
        return {"rule:test": ["subscription_1"]}

    def manual_exposure(output, **_kwargs):
        calls.append("manual_exposure")
        assert output["compiler-source-exclusions"] is True
        output["compiler-manual-exposure"] = True
        return {"groups": ["base"]}

    def browsing_runtime(output, _policies):
        calls.append("browsing_runtime")
        assert output["compiler-manual-exposure"] is True
        output["compiler-browsing-runtime"] = True
        return {"status": "regional_hardened"}

    def validate_surface(output):
        calls.append("validate_surface")
        assert output["compiler-browsing-runtime"] is True

    monkeypatch.setattr(compiler, "generate_config", fake_generate_config)
    monkeypatch.setattr(compiler, "apply_acl4ssr_group_semantics", group_semantics)
    monkeypatch.setattr(compiler, "apply_acl4ssr_source_exclusions", source_exclusions)
    monkeypatch.setattr(compiler, "_expose_manual_provider_choices", manual_exposure)
    monkeypatch.setattr(compiler, "harden_browsing_runtime", browsing_runtime)
    monkeypatch.setattr(compiler, "validate_browsing_public_surface", validate_surface)

    compiled = compiler.compile_runtime_graph(
        root=tmp_path,
        config={},
        policies={"pools": []},
        nodes=[],
        known_source_ids={"subscription_1"},
        acl_groups=[{"display_name": "policy"}],
    )

    assert calls == [
        "draft",
        "group_semantics",
        "source_exclusions",
        "manual_exposure",
        "browsing_runtime",
        "validate_surface",
    ]
    assert compiled.graph.candidate["compiler-browsing-runtime"] is True
    assert compiled.report["proxy_groups"] == 1
    assert compiled.report["source_exclusions"] == {"rule:test": ["subscription_1"]}
    assert compiled.report["manual_provider_exposure"] == {"groups": ["base"]}
    assert compiled.report["browsing_runtime"]["status"] == "regional_hardened"


def test_mihomo_serializer_detaches_compiled_runtime_graph() -> None:
    graph = compiler.RuntimeGraph.from_candidate(
        {
            "proxy-providers": {},
            "proxy-groups": [{"name": "public", "type": "select", "proxies": ["DIRECT"]}],
            "rules": ["MATCH,DIRECT"],
        }
    )

    document = serialize_runtime_graph(graph)
    document["proxy-groups"][0]["proxies"].append("REJECT")

    assert graph.group_members("public") == ("DIRECT",)
    assert document["proxy-groups"][0]["proxies"] == ["DIRECT", "REJECT"]
