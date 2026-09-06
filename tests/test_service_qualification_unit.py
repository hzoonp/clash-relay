from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import clash_relay.service_qualification as services
from clash_relay.errors import ValidationError
from clash_relay.scheduler_policy import AICachePolicy


def test_base_service_contract_defaults_are_conservative(tmp_path: Path) -> None:
    service = services.ServiceQualification("ai_example", "example", "__CR_AI_SERVICE_EXAMPLE")
    policy = AICachePolicy(pass_ttl_seconds=7200, failure_ttl_seconds=900)
    primary = {"url": "https://example.invalid/204"}

    assert service.cache_key() == "ai_example"
    assert service.cache_ttls(policy) == (7200, 900)
    assert service.qualification_probes(primary) == (primary,)
    assert service.supporting_probes() == ()
    assert service.diagnostics_key() is None
    assert service.cache_metadata() == {}
    assert service.route_postprocess(tmp_path / "candidate.yaml") is None
    assert service.supports_client_path_hardening is False
    assert (
        service.build_extended_diagnostics(
            live_tested=1,
            live_qualified={"anonymous"},
            qualification_diagnostics={},
            supporting_diagnostics={},
            supporting_qualified=set(),
        )
        is None
    )


def test_base_service_client_path_hardening_fails_closed(tmp_path: Path) -> None:
    service = services.ServiceQualification("ai_example", "example", "__CR_AI_SERVICE_EXAMPLE")

    with pytest.raises(ValidationError, match="does not support client-path hardening"):
        service.harden_client_path(tmp_path / "candidate.yaml")


def test_unknown_service_probe_fails_closed() -> None:
    with pytest.raises(ValidationError, match="no ServiceQualification implementation"):
        services.service_qualification_by_probe("ai_unknown")


def test_apply_service_route_postprocessing_collects_only_declared_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text("proxy-groups: []\n", encoding="utf-8")

    class Rewriter(services.ServiceQualification):
        def route_postprocess(self, target: Path):
            assert target == candidate
            return "example_route_lock", {"status": "passed"}

    rewriter = Rewriter("ai_example", "example", "__CR_AI_SERVICE_EXAMPLE")
    passive = services.ServiceQualification("ai_passive", "passive", "__CR_AI_SERVICE_PASSIVE")
    monkeypatch.setattr(services, "service_qualifications", lambda: (rewriter, passive))

    assert services.apply_service_route_postprocessing(candidate) == {
        "example_route_lock": {"status": "passed"}
    }


def test_declared_hardening_rejects_known_service_without_implementation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text("proxy-groups: []\n", encoding="utf-8")
    policies = tmp_path / "policies.yaml"
    policies.write_text("version: 2\nfragments: {}\n", encoding="utf-8")
    claude = services.ClaudeQualification()
    monkeypatch.setattr(
        services,
        "load_policy_document",
        lambda _path: SimpleNamespace(
            document={"probes": {"ai_claude": {"client_path_hardening": True}}}
        ),
    )
    monkeypatch.setattr(services, "service_qualifications", lambda: (claude,))

    with pytest.raises(ValidationError, match="does not implement declared client-path hardening"):
        services.harden_declared_service_client_paths(candidate=candidate, policies=policies)


def test_declared_hardening_requires_probe_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text("proxy-groups: []\n", encoding="utf-8")
    policies = tmp_path / "policies.yaml"
    policies.write_text("version: 2\nfragments: {}\n", encoding="utf-8")
    monkeypatch.setattr(
        services,
        "load_policy_document",
        lambda _path: SimpleNamespace(document={"probes": []}),
    )

    with pytest.raises(ValidationError, match="requires policy probes"):
        services.harden_declared_service_client_paths(candidate=candidate, policies=policies)
