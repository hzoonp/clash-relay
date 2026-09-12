from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
import yaml

from clash_relay.builder import build_candidate
from clash_relay.mihomo import validate_with_mihomo
from clash_relay.runtime_graph import RuntimeGraph

pytestmark = pytest.mark.integration


def _binary() -> Path:
    value = os.environ.get("MIHOMO_BIN")
    if not value:
        pytest.skip("MIHOMO_BIN is not set")
    return Path(value)


def _subscription(path: Path, rows: list[dict]) -> str:
    path.write_text(yaml.safe_dump({"proxies": rows}, sort_keys=False), encoding="utf-8")
    return path.resolve().as_uri()


def _http(name: str, server: str, port: int) -> dict:
    return {"name": name, "type": "http", "server": server, "port": port}


def _acl_fixture_fetcher(url: str, **kwargs) -> str:
    # The consumer contract is about generated Mihomo/FlClash-facing structure,
    # not upstream ACL4SSR availability. Keep the rule input deterministic and
    # offline while still exercising the canonical ACL compilation path.
    return "DOMAIN-SUFFIX,fictional-consumer.example\n"


def _canonical_project(repo_root: Path, tmp_path: Path) -> tuple[dict[str, Path], dict[str, str]]:
    root = tmp_path / "canonical-consumer"
    root.mkdir()
    for name in ("config.yaml", "subscriptions.yaml", "policies.yaml"):
        shutil.copy2(repo_root / name, root / name)
    shutil.copytree(repo_root / "policies", root / "policies")
    shutil.copytree(repo_root / "rules", root / "rules")

    config_path = root / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["generation"]["allow_file_subscription_urls"] = True
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    subscription_1 = _subscription(
        root / "subscription-1.yaml",
        [
            _http("US Standard", "sub1-standard.invalid.example", 21001),
            _http("US Exactly 2x", "sub1-two.invalid.example", 21002),
            _http("US 2.01x", "sub1-over.invalid.example", 21003),
            _http("US EMBY 1x", "sub1-emby.invalid.example", 21004),
        ],
    )
    subscription_2 = _subscription(
        root / "subscription-2.yaml",
        [_http("US General 02", "sub2-general.invalid.example", 22001)],
    )
    subscription_3 = _subscription(
        root / "subscription-3.yaml",
        [_http("JP General 03", "sub3-general.invalid.example", 23001)],
    )
    subscription_4 = _subscription(
        root / "subscription-4.yaml",
        [_http("SG General 04", "sub4-general.invalid.example", 24001)],
    )
    return (
        {
            "config_path": config_path,
            "subscriptions_path": root / "subscriptions.yaml",
            "policies_path": root / "policies.yaml",
        },
        {
            "SUBSCRIPTION_1_URL": subscription_1,
            "SUBSCRIPTION_2_URL": subscription_2,
            "SUBSCRIPTION_3_URL": subscription_3,
            "SUBSCRIPTION_4_URL": subscription_4,
        },
    )


def test_flclash_facing_candidate_preserves_source_isolation_and_loads_in_real_mihomo(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    paths, env = _canonical_project(repo_root, tmp_path)
    result = build_candidate(**paths, env=env, rule_fetcher=_acl_fixture_fetcher)

    reports = {row["id"]: row for row in result.report["subscriptions"]}
    restricted = reports["subscription_1"]
    assert restricted["status"] == "ok"
    assert restricted["nodes"] == 2
    assert restricted["filtered_by_name"] == 1
    assert restricted["filtered_over_multiplier"] == 1

    assert "US Standard" in result.yaml_text
    assert "US Exactly 2x" in result.yaml_text
    assert "US 2.01x" not in result.yaml_text
    assert "US EMBY 1x" not in result.yaml_text

    document = yaml.safe_load(result.yaml_text)
    graph = RuntimeGraph.from_candidate(document)
    general = graph.walk_resolved("代理选择")
    assert general.providers
    general_proxy_names = set(general.proxies)
    assert not any("US Standard" in name for name in general_proxy_names)
    assert not any("US Exactly 2x" in name for name in general_proxy_names)
    assert any("US General 02" in name for name in general_proxy_names)

    # FlClash consumes Mihomo configuration. CI can prove that the exact
    # consumer-facing YAML is accepted and starts on the pinned real Mihomo
    # cores; it intentionally does not claim GUI/device-specific FlClash tests.
    candidate = tmp_path / "flclash-facing.yaml"
    candidate.write_text(result.yaml_text, encoding="utf-8")
    report = validate_with_mihomo(_binary(), candidate, startup_seconds=1.0)
    assert report["config_test"] == "passed"
    assert report["startup_smoke"] == "passed"
