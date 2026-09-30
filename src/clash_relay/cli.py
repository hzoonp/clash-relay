"""Command-line interface for local use and GitHub Actions."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import __version__
from .builder import build_candidate
from .config_loader import load_project
from .diagnose import diagnose_candidate
from .doctor import run_doctor
from .effective_config import describe_effective_config
from .errors import ClashRelayError, ValidationError
from .mihomo import load_candidate, validate_with_mihomo
from .production_application import (
    apply_production_release_retention,
    audit_production_release_inventory,
    audit_production_release_state,
    plan_production_release_retention,
    publish_production_release,
    reconcile_production_release,
    reconcile_production_release_ids,
)
from .publication import publication_gate
from .util import atomic_write
from .validator import validate_generated_config


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _add_project_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=_path, default=Path("config.yaml"))
    parser.add_argument("--subscriptions", type=_path, default=Path("subscriptions.yaml"))
    parser.add_argument("--policies", type=_path, default=Path("policies.yaml"))


def _add_build_inputs(parser: argparse.ArgumentParser) -> None:
    _add_project_args(parser)
    parser.add_argument(
        "--secret-file",
        type=_path,
        help="Ignored local YAML/JSON mapping; GitHub Actions should use secrets instead.",
    )


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_report(path: Path | None, report: dict[str, Any]) -> None:
    if path is not None:
        atomic_write(path, _json_text(report))


def _build_from_args(args: argparse.Namespace):
    return build_candidate(
        config_path=args.config,
        subscriptions_path=args.subscriptions,
        policies_path=args.policies,
        secret_file=args.secret_file,
    )


def _command_validate_project(args: argparse.Namespace) -> int:
    project = load_project(
        config_path=args.config,
        subscriptions_path=args.subscriptions,
        policies_path=args.policies,
    )
    summary = {
        "status": "ok",
        "enabled_subscriptions": sum(1 for item in project.subscriptions if item.enabled),
        "pools": len(project.policies["pools"]),
        "chains": len(project.policies["chains"]),
    }
    print(_json_text(summary), end="")
    return 0


def _command_effective_config(args: argparse.Namespace) -> int:
    project = load_project(
        config_path=args.config,
        subscriptions_path=args.subscriptions,
        policies_path=args.policies,
    )
    print(_json_text(describe_effective_config(project)), end="")
    return 0


def _command_doctor(args: argparse.Namespace) -> int:
    report = run_doctor(
        config_path=args.config,
        subscriptions_path=args.subscriptions,
        policies_path=args.policies,
        secret_file=args.secret_file,
        mihomo_manifest=args.mihomo_manifest,
        public_only=args.public_only,
        check_subscriptions=args.check_subscriptions,
        check_cloudflare=args.check_cloudflare,
        check_release_state=args.check_release_state,
    )
    print(_json_text(report), end="")
    return 0 if report["status"] == "passed" else 2


def _command_diagnose(args: argparse.Namespace) -> int:
    report = diagnose_candidate(
        args.candidate,
        mihomo_bin=args.mihomo_bin,
        startup_seconds=args.startup_seconds,
    )
    print(_json_text(report), end="")
    return 0 if report["status"] == "passed" else 2


def _command_generate(args: argparse.Namespace) -> int:
    result = _build_from_args(args)
    _write_report(args.report, result.report)
    if args.check:
        try:
            existing = args.output.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValidationError(f"--check output does not exist: {args.output}") from exc
        if existing != result.yaml_text:
            raise ValidationError("generated output differs from the checked file")
        print(_json_text({"status": "unchanged", **result.report}), end="")
        return 0
    atomic_write(args.output, result.yaml_text)
    print(_json_text({"status": "generated", "output": str(args.output), **result.report}), end="")
    return 0


def _command_build(args: argparse.Namespace) -> int:
    result = _build_from_args(args)
    with tempfile.TemporaryDirectory(prefix="clash-relay-build-") as temp_name:
        candidate = Path(temp_name) / "candidate.yaml"
        candidate.write_text(result.yaml_text, encoding="utf-8")
        mihomo_result = validate_with_mihomo(
            args.mihomo_bin,
            candidate,
            startup_seconds=args.startup_seconds,
            secret_values=result.secret_values,
        )
    atomic_write(args.output, result.yaml_text)
    report = {**result.report, "mihomo": mihomo_result}
    _write_report(args.report, report)
    print(_json_text({"status": "built", "output": str(args.output), **report}), end="")
    return 0


def _command_validate(args: argparse.Namespace) -> int:
    candidate = load_candidate(args.candidate)
    validate_generated_config(candidate)
    result: dict[str, Any] = {"static_validation": "passed"}
    if args.mihomo_bin is not None:
        result["mihomo"] = validate_with_mihomo(
            args.mihomo_bin,
            args.candidate,
            startup_seconds=args.startup_seconds,
        )
    print(_json_text({"status": "valid", **result}), end="")
    return 0


def _command_publication_gate(args: argparse.Namespace) -> int:
    project = load_project(
        config_path=args.config,
        subscriptions_path=args.subscriptions,
        policies_path=args.policies,
    )
    publication_gate(project.config, args.mode, args.acknowledgement)
    print(_json_text({"status": "allowed", "mode": args.mode}), end="")
    return 0


def _command_publish_cloudflare_kv(args: argparse.Namespace) -> int:
    project = load_project(
        config_path=args.config,
        subscriptions_path=args.subscriptions,
        policies_path=args.policies,
    )
    environment = dict(os.environ)
    if args.account_id:
        environment["CLOUDFLARE_ACCOUNT_ID"] = args.account_id
    if args.namespace_title:
        environment["CLOUDFLARE_KV_NAMESPACE_TITLE"] = args.namespace_title
    if args.key:
        project.config["publishing"]["cloudflare_kv"]["key"] = args.key
    result = publish_production_release(
        project=project,
        candidate_path=args.candidate,
        env=environment,
    )
    print(_json_text(result), end="")
    return 0


def _command_reconcile_release(args: argparse.Namespace) -> int:
    project = load_project(
        config_path=args.config,
        subscriptions_path=args.subscriptions,
        policies_path=args.policies,
    )
    if args.candidate_release_id is not None:
        if args.previous is not None:
            raise ValidationError(
                "KV reconciliation requires --previous-release-id or --first-release"
            )
        result = reconcile_production_release_ids(
            project=project,
            candidate_release_id=args.candidate_release_id,
            previous_release_id=args.previous_release_id,
        )
    else:
        if args.previous_release_id is not None:
            raise ValidationError("file reconciliation requires --previous or --first-release")
        result = reconcile_production_release(
            project=project,
            candidate=args.candidate,
            previous=args.previous,
        )
    print(_json_text(result), end="")
    return 0


def _command_plan_release_retention(args: argparse.Namespace) -> int:
    project = load_project(
        config_path=args.config,
        subscriptions_path=args.subscriptions,
        policies_path=args.policies,
    )
    result = plan_production_release_retention(
        project=project,
        retention_days=args.retention_days,
    )
    print(_json_text(result), end="")
    return 0


def _command_audit_release_inventory(args: argparse.Namespace) -> int:
    project = load_project(
        config_path=args.config,
        subscriptions_path=args.subscriptions,
        policies_path=args.policies,
    )
    print(_json_text(audit_production_release_inventory(project=project)), end="")
    return 0


def _command_audit_release_state(args: argparse.Namespace) -> int:
    project = load_project(
        config_path=args.config,
        subscriptions_path=args.subscriptions,
        policies_path=args.policies,
    )
    report = audit_production_release_state(project=project)
    print(_json_text(report), end="")
    return 0 if report["status"] in {"healthy", "first_release"} else 2


def _command_apply_release_retention(args: argparse.Namespace) -> int:
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError("failed to read release retention plan") from exc
    if not isinstance(plan, dict):
        raise ValidationError("release retention plan must be a JSON mapping")
    project = load_project(
        config_path=args.config,
        subscriptions_path=args.subscriptions,
        policies_path=args.policies,
    )
    result = apply_production_release_retention(project=project, plan=plan)
    print(_json_text(result), end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clash-relay",
        description="Generate and validate deterministic, fail-closed Mihomo configurations.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_project = subparsers.add_parser(
        "validate-project", help="Validate public declarations without reading secrets."
    )
    _add_project_args(validate_project)
    validate_project.set_defaults(handler=_command_validate_project)

    effective = subparsers.add_parser(
        "effective-config", help="Explain public network profile overrides and effective DNS."
    )
    _add_project_args(effective)
    effective.set_defaults(handler=_command_effective_config)

    doctor = subparsers.add_parser(
        "doctor", help="Preflight public declarations, private inputs, and optional connectivity."
    )
    _add_project_args(doctor)
    doctor.add_argument(
        "--secret-file",
        type=_path,
        help="Ignored local YAML/JSON subscription secret mapping.",
    )
    doctor.add_argument(
        "--mihomo-manifest",
        type=_path,
        default=Path("tools/mihomo-versions.json"),
    )
    doctor.add_argument(
        "--public-only",
        action="store_true",
        help="Validate only tracked public declarations and the pinned stable Mihomo manifest.",
    )
    doctor.add_argument(
        "--check-subscriptions",
        action="store_true",
        help="Fetch each enabled subscription with the normal bounded production fetch policy.",
    )
    doctor.add_argument(
        "--check-cloudflare",
        action="store_true",
        help="Verify Cloudflare KV read connectivity without publishing any bytes.",
    )
    doctor.add_argument(
        "--check-release-state",
        action="store_true",
        help="Audit production, pointers, and pending release intent without writing.",
    )
    doctor.set_defaults(handler=_command_doctor)

    diagnose = subparsers.add_parser(
        "diagnose",
        help="Run read-only aggregate diagnostics for an existing generated candidate.",
    )
    diagnose.add_argument("--candidate", type=_path, required=True)
    diagnose.add_argument("--mihomo-bin", type=_path)
    diagnose.add_argument("--startup-seconds", type=float, default=1.5)
    diagnose.set_defaults(handler=_command_diagnose)

    generate = subparsers.add_parser(
        "generate", help="Fetch, parse, classify, generate, and statically validate a candidate."
    )
    _add_build_inputs(generate)
    generate.add_argument("--output", type=_path, required=True)
    generate.add_argument("--report", type=_path)
    generate.add_argument(
        "--check",
        action="store_true",
        help="Do not write; fail unless a fresh generation equals --output byte-for-byte.",
    )
    generate.set_defaults(handler=_command_generate)

    build = subparsers.add_parser(
        "build", help="Generate and validate with a real Mihomo core before writing output."
    )
    _add_build_inputs(build)
    build.add_argument("--mihomo-bin", type=_path, required=True)
    build.add_argument("--output", type=_path, required=True)
    build.add_argument("--report", type=_path)
    build.add_argument("--startup-seconds", type=float, default=1.5)
    build.set_defaults(handler=_command_build)

    validate = subparsers.add_parser(
        "validate", help="Statically validate an existing candidate, optionally with Mihomo."
    )
    validate.add_argument("--candidate", type=_path, required=True)
    validate.add_argument("--mihomo-bin", type=_path)
    validate.add_argument("--startup-seconds", type=float, default=1.5)
    validate.set_defaults(handler=_command_validate)

    gate = subparsers.add_parser(
        "publication-gate",
        help="Enforce Artifact/Release/Cloudflare KV publication policy.",
    )
    _add_project_args(gate)
    gate.add_argument(
        "--mode",
        choices=["artifact", "github_release", "cloudflare_kv"],
        required=True,
    )
    gate.add_argument("--acknowledgement", default="")
    gate.set_defaults(handler=_command_publication_gate)

    cloudflare = subparsers.add_parser(
        "publish-cloudflare-kv",
        help="Publish one statically validated candidate to private Cloudflare Workers KV.",
    )
    _add_project_args(cloudflare)
    cloudflare.add_argument("--candidate", type=_path, required=True)
    cloudflare.add_argument("--account-id")
    cloudflare.add_argument("--namespace-title")
    cloudflare.add_argument("--key")
    cloudflare.set_defaults(handler=_command_publish_cloudflare_kv)

    reconcile = subparsers.add_parser(
        "reconcile-release",
        help="Read-only reconciliation of an ambiguous Cloudflare KV release transaction.",
    )
    _add_project_args(reconcile)
    candidate_source = reconcile.add_mutually_exclusive_group(required=True)
    candidate_source.add_argument("--candidate", type=_path)
    candidate_source.add_argument("--candidate-release-id")
    previous = reconcile.add_mutually_exclusive_group(required=True)
    previous.add_argument(
        "--previous",
        type=_path,
        help="Exact production bytes observed before the ambiguous update.",
    )
    previous.add_argument(
        "--previous-release-id",
        help="Verified immutable KV release ID of the pre-attempt production value.",
    )
    previous.add_argument(
        "--first-release",
        action="store_true",
        help="State that the ambiguous attempt had no prior production value.",
    )
    reconcile.set_defaults(handler=_command_reconcile_release)

    retention = subparsers.add_parser(
        "plan-release-retention",
        help="Read-only plan for immutable Cloudflare KV release retention.",
    )
    _add_project_args(retention)
    retention.add_argument("--retention-days", type=int, default=30)
    retention.set_defaults(handler=_command_plan_release_retention)

    inventory = subparsers.add_parser(
        "audit-release-inventory",
        help="Read-only comparison of immutable KV keys, journal, and live pointers.",
    )
    _add_project_args(inventory)
    inventory.set_defaults(handler=_command_audit_release_inventory)

    release_state = subparsers.add_parser(
        "audit-release-state",
        help="Read-only consistency audit of production, pointers, and release transaction.",
    )
    _add_project_args(release_state)
    release_state.set_defaults(handler=_command_audit_release_state)

    apply_retention = subparsers.add_parser(
        "apply-release-retention",
        help="Delete immutable releases covered by a fresh reviewed retention plan.",
    )
    _add_project_args(apply_retention)
    apply_retention.add_argument("--plan", type=_path, required=True)
    apply_retention.add_argument(
        "--confirm-retention-delete",
        action="store_true",
        required=True,
        help="Required acknowledgement before any immutable release deletion.",
    )
    apply_retention.set_defaults(handler=_command_apply_release_retention)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except ClashRelayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
