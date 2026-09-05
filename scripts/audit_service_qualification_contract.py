#!/usr/bin/env python3
"""Fail CI when the P52 ServiceQualification boundary regresses."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def main() -> int:
    registry = _text("src/clash_relay/service_qualification.py")
    ai_application = _text("src/clash_relay/ai_application.py")
    pipeline = _text("src/clash_relay/qualification_pipeline.py")
    scheduling = _text("policies/scheduling.yaml")
    schema = _text("schemas/policies.schema.json")

    for token in (
        "class ServiceQualification",
        "class OpenAIQualification",
        "class ClaudeQualification",
        "class GeminiQualification",
        "service_qualifications()",
        "harden_declared_service_client_paths",
    ):
        if token not in registry:
            raise SystemExit(f"service qualification audit: missing registry contract {token}")

    for forbidden in (
        "openai_application",
        "harden_openai_client_path",
        "ai_openai",
        "ai_claude",
        "ai_gemini",
    ):
        if forbidden in pipeline:
            raise SystemExit(
                f"service qualification audit: main qualification pipeline knows provider {forbidden}"
            )

    if 'if name == "ai_openai"' in ai_application:
        raise SystemExit("service qualification audit: AI orchestration restored provider branching")
    for token in (
        "service_qualification_by_probe",
        "apply_service_route_postprocessing",
    ):
        if token not in ai_application:
            raise SystemExit(f"service qualification audit: AI orchestration bypasses registry {token}")

    if "client_path_hardening: true" not in scheduling:
        raise SystemExit("service qualification audit: client-path hardening is not declarative")
    if '"client_path_hardening": {"type": "boolean"}' not in schema:
        raise SystemExit("service qualification audit: policy schema lacks client-path declaration")

    print("service qualification contract audit: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
