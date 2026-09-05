from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import clash_relay.service_qualification as services
from clash_relay.errors import ValidationError


def test_builtin_service_registry_is_ordered_and_metadata_driven() -> None:
    registry = services.service_qualifications()
    assert [service.probe_name for service in registry] == [
        "ai_openai",
        "ai_claude",
        "ai_gemini",
    ]
    assert services.service_order() == tuple(service.probe_name for service in registry)
    assert services.service_labels() == {
        service.probe_name: service.label for service in registry
    }
    assert services.service_targets() == {
        service.probe_name: service.target_group for service in registry
    }
    assert services.service_qualification_by_probe("ai_claude").label == "claude"


def test_client_path_hardening_is_declarative_and_registry_driven(
    tmp_path: Path, monkeypatch
) -> None:
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text("proxy-groups: []\n", encoding="utf-8")
    policies = tmp_path / "policies.yaml"
    policies.write_text("version: 2\nfragments: {}\n", encoding="utf-8")

    class ExampleService(services.ServiceQualification):
        @property
        def supports_client_path_hardening(self) -> bool:
            return True

        def harden_client_path(self, target: Path) -> dict[str, object]:
            with target.open("a", encoding="utf-8") as handle:
                handle.write("example_hardened: true\n")
            return {"status": "passed"}

    example = ExampleService("ai_example", "example", "__CR_AI_SERVICE_EXAMPLE")
    monkeypatch.setattr(
        services,
        "load_policy_document",
        lambda _path: SimpleNamespace(
            document={
                "probes": {
                    "ai_example": {"client_path_hardening": True},
                    "ai_other": {"client_path_hardening": False},
                }
            }
        ),
    )
    monkeypatch.setattr(services, "service_qualifications", lambda: (example,))

    report = services.harden_declared_service_client_paths(
        candidate=candidate,
        policies=policies,
    )

    assert report == {
        "status": "passed",
        "hardened_services": 1,
        "services": {"example": {"status": "passed"}},
    }
    assert "example_hardened: true" in candidate.read_text(encoding="utf-8")


def test_unsupported_declared_client_path_hardening_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text("proxy-groups: []\n", encoding="utf-8")
    policies = tmp_path / "policies.yaml"
    policies.write_text("version: 2\nfragments: {}\n", encoding="utf-8")
    monkeypatch.setattr(
        services,
        "load_policy_document",
        lambda _path: SimpleNamespace(
            document={"probes": {"ai_unknown": {"client_path_hardening": True}}}
        ),
    )
    monkeypatch.setattr(services, "service_qualifications", lambda: ())

    with pytest.raises(ValidationError, match="unsupported service"):
        services.harden_declared_service_client_paths(candidate=candidate, policies=policies)


def test_qualification_pipeline_has_no_provider_specific_dependency(repo_root: Path) -> None:
    pipeline = (repo_root / "src" / "clash_relay" / "qualification_pipeline.py").read_text(
        encoding="utf-8"
    )
    assert "openai_application" not in pipeline
    assert "harden_openai_client_path" not in pipeline
    assert "ai_openai" not in pipeline
    assert "ai_claude" not in pipeline
    assert "ai_gemini" not in pipeline
    assert "harden_declared_service_client_paths" in pipeline


def test_ai_application_has_no_provider_name_branch(repo_root: Path) -> None:
    application = (repo_root / "src" / "clash_relay" / "ai_application.py").read_text(
        encoding="utf-8"
    )
    assert 'if name == "ai_openai"' not in application
    assert "service_qualification_by_probe" in application
    assert "apply_service_route_postprocessing" in application
