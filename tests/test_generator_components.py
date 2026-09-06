from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from clash_relay.errors import GenerationError
from clash_relay.rule_compiler import RuleCompiler
from clash_relay.runtime_config_renderer import RuntimeConfigRenderer


def test_runtime_config_renderer_preserves_client_owned_dns(repo_root: Path) -> None:
    config = yaml.safe_load((repo_root / "config.yaml").read_text(encoding="utf-8"))

    rendered = RuntimeConfigRenderer().render(config)

    assert config["runtime"]["dns"]["mode"] == "client"
    assert "dns" not in rendered
    assert "store-fake-ip" not in rendered["profile"]
    assert rendered["sniffer"]["enable"] is True


def test_rule_compiler_owns_direct_and_final_rules(repo_root: Path) -> None:
    result = RuleCompiler(repo_root).compile(
        modules={"general": True},
        policies={"pools": []},
        groups=[{"name": "Proxy"}],
        final_target="Proxy",
    )

    assert result.rules[-1] == "MATCH,Proxy"
    assert any(rule.endswith(",DIRECT") for rule in result.rules[:-1])
    assert result.rule_providers == {}


def test_rule_compiler_rejects_unavailable_external_target(repo_root: Path) -> None:
    with pytest.raises(GenerationError, match="targets unavailable group"):
        RuleCompiler(repo_root).compile(
            modules={"general": True},
            policies={"pools": []},
            groups=[{"name": "Proxy"}],
            external_rules=[
                {
                    "source_id": "invalid",
                    "target": "Missing",
                    "priority": 10,
                    "order": 0,
                    "rule": {"type": "DOMAIN", "value": "example.invalid"},
                }
            ],
            final_target="Proxy",
        )


def test_generator_no_longer_owns_runtime_or_rule_loading(repo_root: Path) -> None:
    generator = (repo_root / "src" / "clash_relay" / "generator.py").read_text(encoding="utf-8")

    assert "def _runtime_config(" not in generator
    assert "def _load_rules(" not in generator
    assert "def _render_rule(" not in generator
    assert "RuntimeConfigRenderer().render(config)" in generator
    assert "RuleCompiler(root).compile(" in generator
