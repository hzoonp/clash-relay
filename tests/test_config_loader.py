from __future__ import annotations

from pathlib import Path

import pytest

from clash_relay.config_loader import load_project
from clash_relay.errors import ConfigurationError
from clash_relay.policy_document import load_policy_document
from clash_relay.schema import load_and_validate


def test_all_public_examples_validate(repo_root: Path) -> None:
    load_and_validate(repo_root / "config.example.yaml", "config.schema.json")
    load_and_validate(repo_root / "subscriptions.example.yaml", "subscriptions.schema.json")
    policies = load_policy_document(repo_root / "policies.yaml")
    assert policies.model_version == 2


def test_fixture_project_loads(project_paths: dict[str, Path]) -> None:
    project = load_project(**project_paths)
    assert [item.id for item in project.subscriptions] == ["primary", "secondary", "special"]
    assert {item["id"] for item in project.policies["pools"]} >= {
        "general",
        "chatgpt",
        "claude",
        "gemini",
    }


def test_arbitrary_subscription_count_is_data_driven(project_factory, yaml_editor) -> None:
    _, paths = project_factory()

    def add(document):
        item = dict(document["subscriptions"][1])
        item.update(id="fourth", secret_name="SUB_FOURTH", display_name="Fourth")
        document["subscriptions"].append(item)

    yaml_editor(paths["subscriptions_path"], add)
    project = load_project(**paths)
    assert len(project.subscriptions) == 4


@pytest.mark.parametrize("field", ["id", "secret_name"])
def test_duplicate_subscription_identity_fails(project_factory, yaml_editor, field: str) -> None:
    _, paths = project_factory()

    def duplicate(document):
        document["subscriptions"][1][field] = document["subscriptions"][0][field]

    yaml_editor(paths["subscriptions_path"], duplicate)
    with pytest.raises(ConfigurationError, match="duplicate"):
        load_project(**paths)


def test_unknown_module_fails(project_factory, yaml_editor) -> None:
    _, paths = project_factory()
    yaml_editor(paths["policies_path"], lambda data: data["pools"][0].update(module="missing"))
    with pytest.raises(ConfigurationError, match="undeclared module"):
        load_project(**paths)


def test_unknown_capability_fails(project_factory, yaml_editor) -> None:
    _, paths = project_factory()
    yaml_editor(
        paths["subscriptions_path"],
        lambda data: data["subscriptions"][0]["default_capabilities"].append("unknown"),
    )
    with pytest.raises(ConfigurationError, match="unknown capabilities"):
        load_project(**paths)


def test_unknown_cost_level_fails(project_factory, yaml_editor) -> None:
    _, paths = project_factory()
    yaml_editor(
        paths["subscriptions_path"],
        lambda data: data["subscriptions"][0].update(default_cost_level="gold"),
    )
    with pytest.raises(ConfigurationError, match="unknown cost level"):
        load_project(**paths)


def test_restricted_capability_name_inference_requires_opt_in(project_factory, yaml_editor) -> None:
    _, paths = project_factory()

    def mutate(data):
        data["subscriptions"][0]["name_rules"] = [
            {"pattern": "home", "add_capabilities": ["residential"]}
        ]

    yaml_editor(paths["subscriptions_path"], mutate)
    with pytest.raises(ConfigurationError, match="restricted"):
        load_project(**paths)


def test_restricted_capability_name_inference_explicit_opt_in_loads(
    project_factory, yaml_editor
) -> None:
    _, paths = project_factory()

    def mutate(data):
        data["subscriptions"][0]["name_rules"] = [
            {
                "pattern": "home",
                "add_capabilities": ["residential"],
                "allow_restricted_capabilities": True,
            }
        ]

    yaml_editor(paths["subscriptions_path"], mutate)
    load_project(**paths)


def test_invalid_name_rule_regex_fails(project_factory, yaml_editor) -> None:
    _, paths = project_factory()
    yaml_editor(
        paths["subscriptions_path"],
        lambda data: data["subscriptions"][0].update(name_rules=[{"pattern": "["}]),
    )
    with pytest.raises(ConfigurationError, match="invalid name rule"):
        load_project(**paths)


def test_invalid_country_classifier_regex_fails(project_factory, yaml_editor) -> None:
    _, paths = project_factory()
    yaml_editor(
        paths["policies_path"],
        lambda data: data["country_classification"]["aliases"]["US"].append("["),
    )
    with pytest.raises(ConfigurationError, match="invalid regex"):
        load_project(**paths)


def test_fallback_outside_regions_fails(project_factory, yaml_editor) -> None:
    _, paths = project_factory()
    yaml_editor(
        paths["policies_path"],
        lambda data: data["pools"][0].update(fallback_order=["US"]),
    )
    with pytest.raises(ConfigurationError, match="fallback_order"):
        load_project(**paths)


def test_rule_path_traversal_fails(project_factory, yaml_editor) -> None:
    _, paths = project_factory()
    yaml_editor(
        paths["policies_path"],
        lambda data: data["pools"][1].update(rules="../outside.yaml"),
    )
    with pytest.raises(ConfigurationError, match="escapes"):
        load_project(**paths)


def test_missing_rule_file_fails(project_factory, yaml_editor) -> None:
    _, paths = project_factory()
    yaml_editor(
        paths["policies_path"],
        lambda data: data["pools"][1].update(rules="rules/missing.yaml"),
    )
    with pytest.raises(ConfigurationError, match="does not exist"):
        load_project(**paths)


def test_invalid_expected_status_fails(project_factory, yaml_editor) -> None:
    _, paths = project_factory()
    yaml_editor(
        paths["policies_path"],
        lambda data: data["probes"]["chatgpt"].update(expected_status="999"),
    )
    with pytest.raises(ConfigurationError, match="expected_status"):
        load_project(**paths)


def test_probe_method_must_be_head(project_factory, yaml_editor) -> None:
    _, paths = project_factory()
    yaml_editor(
        paths["policies_path"],
        lambda data: data["probes"]["chatgpt"].update(method="GET"),
    )
    with pytest.raises(ConfigurationError, match="schema validation"):
        load_project(**paths)


def test_public_subscription_url_field_is_rejected(project_factory, yaml_editor) -> None:
    _, paths = project_factory()
    yaml_editor(
        paths["subscriptions_path"],
        lambda data: data["subscriptions"][0].update(url="https://secret.invalid/token"),
    )
    with pytest.raises(ConfigurationError, match="Additional properties"):
        load_project(**paths)


def test_missing_direct_rules_fails(project_factory) -> None:
    root, paths = project_factory()
    (root / "rules/direct.yaml").unlink()
    with pytest.raises(ConfigurationError, match=r"direct\.yaml"):
        load_project(**paths)


def test_yaml_alias_is_rejected(project_factory) -> None:
    _, paths = project_factory()
    paths["config_path"].write_text("version: &v 1\nruntime: *v\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="anchors and aliases"):
        load_project(**paths)
