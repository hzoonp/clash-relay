from __future__ import annotations

import json
from pathlib import Path

import pytest

import clash_relay.doctor as doctor_module
from clash_relay.doctor import run_doctor
from clash_relay.errors import FetchError, PublicationError, SecretError, ValidationError


def _paths(repo_root: Path) -> dict[str, Path]:
    return {
        "config_path": repo_root / "config.yaml",
        "subscriptions_path": repo_root / "subscriptions.yaml",
        "policies_path": repo_root / "policies.yaml",
        "mihomo_manifest": repo_root / "tools/mihomo-versions.json",
    }


def _private_env() -> dict[str, str]:
    return {
        "CLASH_RELAY_SUBSCRIPTIONS": json.dumps(
            {
                "SUBSCRIPTION_1_URL": "https://secret-one.example/sub",
                "SUBSCRIPTION_2_URL": "https://secret-two.example/sub",
                "SUBSCRIPTION_3_URL": "https://secret-three.example/sub",
                "SUBSCRIPTION_4_URL": "https://secret-four.example/sub",
                "SUBSCRIPTION_5_URL": "https://secret-five.example/sub",
            }
        )
    }


def test_public_only_doctor_validates_tracked_contract(repo_root: Path) -> None:
    report = run_doctor(**_paths(repo_root), public_only=True, env={})

    assert report["status"] == "passed"
    assert report["public"]["enabled_subscriptions"] == 5
    assert report["public"]["stable_mihomo_cores"] >= 1
    assert report["public"]["scheduler_policy_declared"] is True
    assert report["public"]["policy_model_version"] == 2
    assert report["public"]["policy_model_status"] == "current"
    assert report["download_routing"]["declaration_check"]["status"] == "passed"
    assert report["subscriptions"]["status"] == "skipped"
    assert report["cloudflare"]["status"] == "skipped"


def test_private_readiness_never_serializes_subscription_urls(repo_root: Path) -> None:
    environment = _private_env()
    report = run_doctor(**_paths(repo_root), env=environment)
    serialized = json.dumps(report, sort_keys=True)

    assert report["subscriptions"] == {"status": "ready", "enabled": 5, "resolved": 5}
    assert report["cloudflare"]["status"] == "skipped"
    for secret in (
        "secret-one.example",
        "secret-two.example",
        "secret-three.example",
        "secret-four.example",
        "secret-five.example",
    ):
        assert secret not in serialized


def test_release_state_readiness_is_opt_in_and_blocks_inconsistent_state(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = {
        **_private_env(),
        "CLOUDFLARE_API_TOKEN": "private-token",
        "CLOUDFLARE_ACCOUNT_ID": "account",
        "CLOUDFLARE_KV_NAMESPACE_TITLE": "namespace",
    }
    observed: list[str] = []

    def audit(*, project, env):
        observed.append(str(project.config["publishing"]["cloudflare_kv"]["key"]))
        assert env is environment
        return {
            "status": "inconsistent",
            "production_matches_current": False,
            "previous_available": True,
            "transaction": "pending",
            "mutation": "none",
        }

    monkeypatch.setattr(doctor_module, "audit_production_release_state", audit)
    report = run_doctor(**_paths(repo_root), env=environment, check_release_state=True)

    assert observed == ["production-config"]
    assert report["status"] == "blocked"
    assert report["release_state"]["transaction"] == "pending"
    assert "private-token" not in json.dumps(report)


def test_doctor_fails_early_on_missing_subscription_secret(repo_root: Path) -> None:
    environment = _private_env()
    mapping = json.loads(environment["CLASH_RELAY_SUBSCRIPTIONS"])
    mapping.pop("SUBSCRIPTION_4_URL")
    environment["CLASH_RELAY_SUBSCRIPTIONS"] = json.dumps(mapping)

    with pytest.raises(SecretError, match="SUBSCRIPTION_4_URL"):
        run_doctor(**_paths(repo_root), env=environment)


def test_subscription_connectivity_reports_counts_only(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_fetch(url: str, **kwargs) -> str:
        calls.append(url)
        return "PRIVATE SUBSCRIPTION PAYLOAD"

    monkeypatch.setattr(doctor_module, "fetch_subscription", fake_fetch)
    environment = _private_env()
    report = run_doctor(
        **_paths(repo_root),
        env=environment,
        check_subscriptions=True,
    )
    serialized = json.dumps(report, sort_keys=True)

    assert len(calls) == 5
    assert report["subscriptions"]["reachable"] == 5
    assert "PRIVATE SUBSCRIPTION PAYLOAD" not in serialized
    assert "secret-one.example" not in serialized


def test_subscription_connectivity_uses_each_source_client_profile(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, str] = {}

    def fake_fetch(url: str, **kwargs) -> str:
        seen[url] = kwargs["client_profile"]
        if "secret-one.example" in url and kwargs["client_profile"] != "mihomo":
            raise FetchError("Mihomo User-Agent required")
        return "fictional subscription"

    monkeypatch.setattr(doctor_module, "fetch_subscription", fake_fetch)
    report = run_doctor(**_paths(repo_root), env=_private_env(), check_subscriptions=True)

    assert report["subscriptions"]["reachable"] == 5
    assert seen["https://secret-one.example/sub"] == "mihomo"
    assert set(seen.values()) == {"mihomo", "default"}


def test_subscription_connectivity_failure_redacts_private_hostname(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_fetch(url: str, **kwargs) -> str:
        raise FetchError(f"PRIVATE failure for {url}")

    monkeypatch.setattr(doctor_module, "fetch_subscription", fake_fetch)
    with pytest.raises(ValidationError) as caught:
        run_doctor(
            **_paths(repo_root),
            env=_private_env(),
            check_subscriptions=True,
        )

    message = str(caught.value)
    assert "subscription_1" in message
    assert "secret-one.example" not in message
    assert "PRIVATE failure" not in message


def test_cloudflare_check_is_read_only_and_safe(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads: list[str] = []

    class FakePublisher:
        def __init__(
            self,
            *,
            token: str,
            account_id: str,
            namespace_title: str,
            key_name: str,
        ):
            assert token == "PRIVATE-TOKEN"
            assert account_id == "PRIVATE-ACCOUNT"
            assert namespace_title == "PRIVATE-NAMESPACE"
            reads.append(key_name)

        def read(self) -> bytes:
            return b"PRIVATE CONFIG BYTES"

        def publish(self, *, content: bytes):
            raise AssertionError("doctor must never publish")

    monkeypatch.setattr(doctor_module, "CloudflareKVPublisher", FakePublisher)
    environment = {
        **_private_env(),
        "CLOUDFLARE_API_TOKEN": "PRIVATE-TOKEN",
        "CLOUDFLARE_ACCOUNT_ID": "PRIVATE-ACCOUNT",
        "CLOUDFLARE_KV_NAMESPACE_TITLE": "PRIVATE-NAMESPACE",
    }
    report = run_doctor(**_paths(repo_root), env=environment, check_cloudflare=True)
    serialized = json.dumps(report, sort_keys=True)

    assert reads == ["production-config"]
    assert report["cloudflare"] == {"status": "ready", "production_key_present": True}
    for secret in (
        "PRIVATE-TOKEN",
        "PRIVATE-ACCOUNT",
        "PRIVATE-NAMESPACE",
        "PRIVATE CONFIG BYTES",
    ):
        assert secret not in serialized


def test_cloudflare_failure_redacts_connector_details(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakePublisher:
        def __init__(self, **kwargs):
            pass

        def read(self) -> bytes:
            raise PublicationError("PRIVATE-TOKEN PRIVATE-ACCOUNT PRIVATE-NAMESPACE")

    monkeypatch.setattr(doctor_module, "CloudflareKVPublisher", FakePublisher)
    environment = {
        **_private_env(),
        "CLOUDFLARE_API_TOKEN": "PRIVATE-TOKEN",
        "CLOUDFLARE_ACCOUNT_ID": "PRIVATE-ACCOUNT",
        "CLOUDFLARE_KV_NAMESPACE_TITLE": "PRIVATE-NAMESPACE",
    }
    with pytest.raises(ValidationError) as caught:
        run_doctor(**_paths(repo_root), env=environment, check_cloudflare=True)

    message = str(caught.value)
    assert message == "Cloudflare KV read readiness check failed"
    assert "PRIVATE" not in message


def test_public_only_rejects_private_connectivity_flags(repo_root: Path) -> None:
    with pytest.raises(ValidationError, match="public-only"):
        run_doctor(**_paths(repo_root), public_only=True, check_cloudflare=True, env={})
