"""VMess subscription parsing must fail inside the project's failure boundary.

R03: a share link is untrusted JSON. Non-finite numbers and deeply nested JSON
used to escape as ``OverflowError``/``RecursionError`` instead of being
normalized to a subscription parse failure.

R04: ``net=grpc`` share links carry the gRPC service name in ``path``; it must
survive as ``grpc-opts.grpc-service-name`` so Mihomo routes to the same service.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
import yaml

from clash_relay.builder import build_candidate
from clash_relay.errors import GenerationError, SubscriptionError
from clash_relay.subscription_parser import parse_subscription


def _vmess(**fields: object) -> str:
    payload = {
        "v": "2",
        "ps": "fixture",
        "add": "node.invalid.example",
        "port": "443",
        "id": "00000000-0000-4000-8000-000000000001",
        "aid": "0",
        "net": "tcp",
    }
    payload.update(fields)
    return "vmess://" + base64.b64encode(json.dumps(payload).encode()).decode()


def _deep_json_uri(depth: int) -> str:
    body = "[" * depth + "]" * depth
    return "vmess://" + base64.b64encode(body.encode()).decode()


@pytest.mark.parametrize("value", [1e999, -1e999])
def test_non_finite_port_is_normalized_under_skip_policy(value: float) -> None:
    result = parse_subscription(_vmess(port=value), invalid_policy="skip")

    assert result.proxies == ()
    assert result.skipped_items == 1


def test_non_finite_port_fails_closed_under_error_policy() -> None:
    with pytest.raises(SubscriptionError, match="invalid proxy URI"):
        parse_subscription(_vmess(port=1e999))


@pytest.mark.parametrize("value", [1e999, -1e999])
def test_non_finite_alter_id_is_normalized_under_skip_policy(value: float) -> None:
    result = parse_subscription(_vmess(aid=value), invalid_policy="skip")

    assert result.proxies == ()
    assert result.skipped_items == 1


@pytest.mark.parametrize(
    "value",
    [1e999, "abc", "443.5", True, ["443"], {"port": 443}],
)
def test_malformed_port_types_never_escape_the_parser(value: object) -> None:
    result = parse_subscription(_vmess(port=value), invalid_policy="skip")

    assert result.proxies == ()
    assert result.skipped_items == 1


def test_missing_port_is_reported_as_an_invalid_port() -> None:
    payload = {
        "v": "2",
        "ps": "fixture",
        "add": "node.invalid.example",
        "id": "00000000-0000-4000-8000-000000000001",
        "aid": "0",
        "net": "tcp",
    }
    uri = "vmess://" + base64.b64encode(json.dumps(payload).encode()).decode()

    with pytest.raises(SubscriptionError, match="invalid_port"):
        parse_subscription(uri)


def test_deeply_nested_vmess_json_is_normalized_not_recursive() -> None:
    result = parse_subscription(_deep_json_uri(60_000), invalid_policy="skip")

    assert result.proxies == ()
    assert result.skipped_items == 1


def test_skip_policy_keeps_normal_sibling_nodes() -> None:
    bad = _vmess(ps="bad", port=1e999)
    good = _vmess(ps="good", add="good.invalid.example")

    result = parse_subscription(f"{bad}\n{good}", invalid_policy="skip")

    assert [proxy["name"] for proxy in result.proxies] == ["good"]
    assert result.skipped_items == 1


def test_error_policy_fails_the_whole_list_when_one_uri_is_invalid() -> None:
    bad = _vmess(ps="bad", port=1e999)
    good = _vmess(ps="good", add="good.invalid.example")

    with pytest.raises(SubscriptionError, match="invalid proxy URI"):
        parse_subscription(f"{bad}\n{good}")


def test_normal_vmess_fields_are_unchanged() -> None:
    proxy = parse_subscription(
        _vmess(port=31002, aid=4, scy="aes-128-gcm", tls="tls", sni="sni.invalid.example")
    ).proxies[0]

    assert proxy["type"] == "vmess"
    assert proxy["port"] == 31002
    assert proxy["alterId"] == 4
    assert proxy["cipher"] == "aes-128-gcm"
    assert proxy["tls"] is True
    assert proxy["servername"] == "sni.invalid.example"
    assert proxy["network"] == "tcp"


def test_vmess_grpc_maps_path_to_grpc_service_name() -> None:
    proxy = parse_subscription(
        _vmess(net="grpc", path="fixture-service", tls="tls", sni="grpc.invalid.example")
    ).proxies[0]

    assert proxy["network"] == "grpc"
    assert proxy["grpc-opts"] == {"grpc-service-name": "fixture-service"}
    assert proxy["tls"] is True
    assert proxy["servername"] == "grpc.invalid.example"


def test_vmess_grpc_without_service_name_omits_grpc_options() -> None:
    proxy = parse_subscription(_vmess(net="grpc", path="")).proxies[0]

    assert proxy["network"] == "grpc"
    assert "grpc-opts" not in proxy


def test_vmess_websocket_and_tcp_nodes_do_not_regress() -> None:
    ws = parse_subscription(_vmess(net="ws", path="/ws", host="edge.invalid.example")).proxies[0]
    tcp = parse_subscription(_vmess(net="tcp")).proxies[0]

    assert ws["ws-opts"] == {"path": "/ws", "headers": {"Host": "edge.invalid.example"}}
    assert "grpc-opts" not in ws
    assert tcp["network"] == "tcp"
    assert "ws-opts" not in tcp
    assert "grpc-opts" not in tcp


def test_grpc_service_name_survives_yaml_round_trip() -> None:
    proxy = parse_subscription(_vmess(net="grpc", path="fixture-service")).proxies[0]

    serialized = yaml.safe_load(yaml.safe_dump(proxy, sort_keys=True))

    assert serialized["grpc-opts"] == {"grpc-service-name": "fixture-service"}


def test_grpc_fixture_matches_runtime_contract() -> None:
    fixture = yaml.safe_load(
        (Path(__file__).parent / "fixtures" / "uri" / "vmess-grpc.yaml").read_text(encoding="utf-8")
    )
    proxy = parse_subscription(fixture["uri"]).proxies[0]

    for field, expected in fixture["expected"].items():
        assert proxy[field] == expected


def test_builder_contains_a_malformed_vmess_uri_in_an_optional_source(
    project_factory, fixture_env, yaml_editor
) -> None:
    _root, paths = project_factory()
    yaml_editor(
        paths["subscriptions_path"],
        lambda document: [
            item.update(enabled=item["id"] in {"primary", "secondary"})
            for item in document["subscriptions"]
        ],
    )
    valid = "proxies:\n  - name: Fictional General US 01\n    type: http\n    server: ok.invalid.example\n    port: 21001\n"
    broken = _vmess(ps="broken", port=1e999) + "\n"

    def fetcher(url: str, **_kwargs) -> str:
        if url == fixture_env["SUB_PRIMARY"]:
            return valid
        if url == fixture_env["SUB_SECONDARY"]:
            return broken
        raise AssertionError(url)

    result = build_candidate(**paths, env=fixture_env, fetcher=fetcher)

    reports = {item["id"]: item for item in result.report["subscriptions"]}
    assert reports["primary"]["status"] == "ok"
    assert reports["secondary"]["status"] == "failed"
    assert reports["secondary"]["failure_category"] == "subscription_parse"


def test_builder_skip_policy_keeps_the_valid_sibling_of_a_malformed_vmess(
    project_factory, fixture_env, yaml_editor
) -> None:
    _root, paths = project_factory()
    yaml_editor(
        paths["subscriptions_path"],
        lambda document: [
            item.update(enabled=item["id"] == "primary") for item in document["subscriptions"]
        ],
    )

    def configure_generation(document):
        document["generation"]["invalid_proxy_policy"] = "skip"

    yaml_editor(paths["config_path"], configure_generation)
    valid_uri = (
        "trojan://fixture-password@trojan.invalid.example:31004"
        "?sni=trojan.invalid.example#Fixture%20Trojan"
    )
    payload = valid_uri + "\n" + _vmess(ps="broken", port=1e999) + "\n"

    result = build_candidate(
        **paths,
        env=fixture_env,
        fetcher=lambda _url, **_kwargs: payload,
    )

    report = next(item for item in result.report["subscriptions"] if item["id"] == "primary")
    assert report["status"] == "ok"
    assert report["parsed_valid_nodes"] == 1
    assert report["skipped_invalid_nodes"] == 1


def test_builder_still_fails_a_required_source_with_a_malformed_vmess(
    project_factory, fixture_env, yaml_editor
) -> None:
    _root, paths = project_factory()
    yaml_editor(
        paths["subscriptions_path"],
        lambda document: [
            item.update(enabled=item["id"] == "primary") for item in document["subscriptions"]
        ],
    )

    with pytest.raises(GenerationError):
        build_candidate(
            **paths,
            env=fixture_env,
            fetcher=lambda _url, **_kwargs: _vmess(ps="broken", port=1e999) + "\n",
        )
