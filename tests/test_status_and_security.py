from __future__ import annotations

import json
from pathlib import Path

import pytest

from clash_relay.builder import build_candidate
from clash_relay.errors import (
    ConfigurationError,
    FetchError,
    GenerationError,
    PublicationError,
    SecretError,
)
from clash_relay.fetch import fetch_subscription, validate_subscription_url
from clash_relay.models import SubscriptionSpec
from clash_relay.publication import ACKNOWLEDGEMENT, publication_gate
from clash_relay.redact import redact_text, redact_url
from clash_relay.secrets import load_secret_mapping, resolve_subscription_urls
from clash_relay.status import parse_expected_status, status_allowed


@pytest.mark.parametrize(
    ("expression", "present", "absent"),
    [
        ("204", {204}, {200, 401}),
        ("200/204/401", {200, 204, 401}, {201, 500}),
        ("200, 204 401", {200, 204, 401}, {404}),
        ("200-202", {200, 201, 202}, {203}),
    ],
)
def test_expected_status_parser(expression: str, present: set[int], absent: set[int]) -> None:
    values = parse_expected_status(expression)
    assert present <= values
    assert not (absent & values)


@pytest.mark.parametrize("expression", ["", "999", "abc", "300-200", "100-500"])
def test_invalid_expected_status_rejected(expression: str) -> None:
    with pytest.raises(ConfigurationError):
        parse_expected_status(expression)


def test_status_allowed() -> None:
    assert status_allowed("200/401", 401)
    assert not status_allowed("200/401", 403)


def test_redaction_removes_full_secret_and_query_values() -> None:
    text = "failed token=abcdefghi password=hunter2 Authorization: Bearer token-value secret-value"
    redacted = redact_text(text, ["secret-value", "hunter2"])
    assert "secret-value" not in redacted
    assert "hunter2" not in redacted
    assert "abcdefghi" not in redacted
    assert "token-value" not in redacted
    assert redacted.count("<redacted>") >= 3


def test_short_secret_does_not_corrupt_password_key() -> None:
    redacted = redact_text("password=long-value", ["pass"])
    assert "password=<redacted>" in redacted


def test_url_redaction_preserves_only_location_shape() -> None:
    url = "https://user:pass@example.invalid/path/token" + "?key=secret#fragment"
    redacted = redact_url(url)
    assert redacted == "https://example.invalid/<redacted>?<redacted>#<redacted>"


@pytest.mark.parametrize(
    ("url", "allow_http", "allow_file"),
    [
        ("http://example.invalid/sub", False, False),
        ("ftp://example.invalid/sub", True, True),
        ("file:///tmp/sub", False, False),
        ("https://user:pass@example.invalid/sub", False, False),
        ("https://127.0.0.1/sub", False, False),
        ("https://10.0.0.1/sub", False, False),
    ],
)
def test_unsafe_subscription_urls_rejected(url: str, allow_http: bool, allow_file: bool) -> None:
    with pytest.raises(FetchError):
        validate_subscription_url(url, allow_http=allow_http, allow_file=allow_file)


def test_https_domain_subscription_url_allowed() -> None:
    validate_subscription_url(
        "https://subscription.invalid.example/path", allow_http=False, allow_file=False
    )


def test_file_subscription_fetch_is_explicit_and_bounded(tmp_path: Path) -> None:
    path = tmp_path / "source.yaml"
    path.write_text("proxies: []\n", encoding="utf-8")
    content = fetch_subscription(
        path.resolve().as_uri(),
        timeout=1,
        max_bytes=1024,
        allow_http=False,
        allow_file=True,
    )
    assert content == "proxies: []\n"
    with pytest.raises(FetchError, match="byte limit"):
        fetch_subscription(
            path.resolve().as_uri(),
            timeout=1,
            max_bytes=4,
            allow_http=False,
            allow_file=True,
        )


def test_secret_bundle_accepts_json_and_yaml(tmp_path: Path) -> None:
    json_mapping = load_secret_mapping(
        env={"CLASH_RELAY_SUBSCRIPTIONS": '{"SUB_A":"https://a.invalid"}'}
    )
    assert json_mapping["SUB_A"] == "https://a.invalid"
    path = tmp_path / "secret.yaml"
    path.write_text("SUB_B:\n  url: https://b.invalid\n", encoding="utf-8")
    yaml_mapping = load_secret_mapping(path, env={})
    assert yaml_mapping["SUB_B"] == "https://b.invalid"


def _spec(name: str = "SUB_A") -> SubscriptionSpec:
    return SubscriptionSpec(
        id="a",
        display_name="A",
        enabled=True,
        required=True,
        secret_name=name,
        priority=1,
        on_error="fail",
        allowed_uses=frozenset({"general"}),
        allowed_countries=frozenset({"OTHER"}),
        default_capabilities=frozenset({"general"}),
        default_cost_level="standard",
    )


def test_direct_environment_secret_resolves() -> None:
    resolved, values = resolve_subscription_urls([_spec()], env={"SUB_A": "https://a.invalid"})
    assert resolved == {"a": "https://a.invalid"}
    assert values == ("https://a.invalid",)


def test_secret_bundle_resolves_arbitrary_keys() -> None:
    bundle = json.dumps({"SUB_A": "https://a.invalid", "SUB_DYNAMIC": "https://d.invalid"})
    resolved, _ = resolve_subscription_urls([_spec()], env={"CLASH_RELAY_SUBSCRIPTIONS": bundle})
    assert resolved["a"] == "https://a.invalid"


def test_missing_secret_reports_name_not_value() -> None:
    with pytest.raises(SecretError, match="SUB_A"):
        resolve_subscription_urls([_spec()], env={})


def _publishing_config() -> dict:
    return {
        "publishing": {
            "artifact": True,
            "github_release": {
                "enabled": False,
                "allow_sensitive_public_release": False,
            },
            "gist": {"enabled": False, "allow_sensitive_unlisted_gist": False},
        }
    }


def test_artifact_publication_is_default() -> None:
    publication_gate(_publishing_config(), "artifact")


def test_release_requires_enablement_and_acknowledgement() -> None:
    config = _publishing_config()
    with pytest.raises(PublicationError, match="acknowledgement"):
        publication_gate(config, "github_release", "")
    config["publishing"]["github_release"].update(enabled=True, allow_sensitive_public_release=True)
    publication_gate(config, "github_release", ACKNOWLEDGEMENT)


def test_gist_requires_explicit_sensitive_opt_in() -> None:
    config = _publishing_config()
    config["publishing"]["gist"]["enabled"] = True
    with pytest.raises(PublicationError, match="allow_sensitive"):
        publication_gate(config, "gist", ACKNOWLEDGEMENT)


def test_optional_subscription_failure_is_skipped(
    project_factory, fixture_env, yaml_editor
) -> None:
    _, paths = project_factory()

    def optional_only(document):
        document["subscriptions"][1]["secret_name"] = "SUB_BROKEN"
        document["subscriptions"][1]["on_error"] = "skip"

    yaml_editor(paths["subscriptions_path"], optional_only)
    env = dict(fixture_env)
    env["SUB_BROKEN"] = "file:///definitely/not/present.yaml"
    result = build_candidate(**paths, env=env)
    failed = [item for item in result.report["subscriptions"] if item["id"] == "secondary"]
    assert failed[0]["status"] == "failed"
    assert "not/present" not in json.dumps(failed)


def test_required_subscription_failure_aborts(project_factory, fixture_env, yaml_editor) -> None:
    _, paths = project_factory()
    yaml_editor(
        paths["subscriptions_path"],
        lambda data: data["subscriptions"][0].update(secret_name="SUB_BROKEN"),
    )
    env = dict(fixture_env)
    env["SUB_BROKEN"] = "file:///definitely/not/present.yaml"
    with pytest.raises(GenerationError, match="subscription 'primary' failed"):
        build_candidate(**paths, env=env)


def test_minimum_successful_subscriptions_gate(project_factory, fixture_env, yaml_editor) -> None:
    _, paths = project_factory()
    yaml_editor(
        paths["config_path"],
        lambda data: data["generation"].update(minimum_successful_subscriptions=3),
    )
    yaml_editor(
        paths["subscriptions_path"],
        lambda data: data["subscriptions"][1].update(secret_name="SUB_BROKEN"),
    )
    env = dict(fixture_env)
    env["SUB_BROKEN"] = "file:///definitely/not/present.yaml"
    with pytest.raises(GenerationError, match="minimum_successful"):
        build_candidate(**paths, env=env)
