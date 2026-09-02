#!/usr/bin/env python3
"""Audit production source-use isolation and emit only aggregate statistics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from clash_relay.acl4ssr_reference import validate_acl4ssr_fidelity
from clash_relay.ai_runtime_reliability import audit_openai_client_path
from clash_relay.config_loader import load_project
from clash_relay.mihomo import load_candidate
from clash_relay.openai_app_contract import audit_route_lock
from clash_relay.production_audit import (
    audit_production_candidate,
    render_production_summary_markdown,
)
from clash_relay.routing_v2_audit import audit_routing_v2
from clash_relay.util import atomic_write


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--subscriptions", type=Path, default=Path("subscriptions.yaml"))
    parser.add_argument("--services", type=Path, default=Path("services.yaml"))
    parser.add_argument("--policies", type=Path, default=Path("policies.yaml"))
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--markdown", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    project = load_project(
        config_path=args.config,
        subscriptions_path=args.subscriptions,
        services_path=args.services,
        policies_path=args.policies,
    )
    candidate = load_candidate(args.candidate)
    build_report = None
    if args.report is not None:
        build_report = json.loads(args.report.read_text(encoding="utf-8"))
    summary = audit_production_candidate(project, candidate, build_report=build_report)
    summary["routing_v2"] = audit_routing_v2(project, candidate)
    summary["openai_app"] = audit_route_lock(candidate)
    summary["openai_client_path"] = audit_openai_client_path(candidate)
    if project.acl4ssr is not None and project.acl4ssr.get("reference") is not None:
        reference_path = args.config.resolve().parent / "rules/acl4ssr-online.reference.ini"
        reference_text = reference_path.read_text(encoding="utf-8")
        summary["acl4ssr_fidelity"] = validate_acl4ssr_fidelity(
            project.acl4ssr,
            reference_text=reference_text,
        )
    if args.markdown is not None:
        atomic_write(args.markdown, render_production_summary_markdown(summary))
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
