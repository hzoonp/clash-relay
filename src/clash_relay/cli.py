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
from .errors import ClashRelayError, ValidationError
from .mihomo import load_candidate, validate_with_mihomo
from .publication import ACKNOWLEDGEMENT, publication_gate
from .publishers.gist import GistPublisher
from .util import atomic_write
from .validator import validate_generated_config


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _add_project_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=_path, default=Path("config.yaml"))
    parser.add_argument("--subscriptions", type=_path, default=Path("subscriptions.yaml"))
    parser.add_argument("--services", type=_path, default=Path("services.yaml"))
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


def _build_from_args(args: argparse.Namespace):  # noqa: ANN202
    return build_candidate(
        config_path=args.config,
        subscriptions_path=args.subscriptions,
        services_path=args.services,
        policies_path=args.policies,
        secret_file=args.secret_file,
    )


def _command_validate_project(args: argparse.Namespace) -> int:
    project = load_project(
        config_path=args.config,
        subscriptions_path=args.subscriptions,
        services_path=args.services,
        policies_path=args.policies,
    )
    summary = {
        "status": "ok",
        "enabled_subscriptions": sum(1 for item in project.subscriptions if item.enabled),
        "services": len(project.services["services"]),
        "pools": len(project.policies["pools"]),
        "chains": len(project.policies["chains"]),
    }
    print(_json_text(summary), end="")
    return 0


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
        services_path=args.services,
        policies_path=args.policies,
    )
    publication_gate(project.config, args.mode, args.acknowledgement)
    print(_json_text({"status": "allowed", "mode": args.mode}), end="")
    return 0


def _command_publish_gist(args: argparse.Namespace) -> int:
    project = load_project(
        config_path=args.config,
        subscriptions_path=args.subscriptions,
        services_path=args.services,
        policies_path=args.policies,
    )
    publication_gate(project.config, "gist", args.acknowledgement)
    token = args.token or os.environ.get("GITHUB_GIST_TOKEN", "")
    gist_id = args.gist_id or os.environ.get("GITHUB_GIST_ID", "")
    content = args.candidate.read_text(encoding="utf-8")
    identifier = GistPublisher(token=token, gist_id=gist_id).publish(
        filename=args.filename,
        content=content,
    )
    print(_json_text({"status": "published", "backend": "gist", "id": identifier}), end="")
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
        "publication-gate", help="Enforce artifact/Release/Gist publication acknowledgements."
    )
    _add_project_args(gate)
    gate.add_argument("--mode", choices=["artifact", "github_release", "gist"], required=True)
    gate.add_argument("--acknowledgement", default="")
    gate.set_defaults(handler=_command_publication_gate)

    gist = subparsers.add_parser("publish-gist", help="Publish a validated candidate to a Gist.")
    _add_project_args(gist)
    gist.add_argument("--candidate", type=_path, required=True)
    gist.add_argument("--filename", default="config.yaml")
    gist.add_argument("--token")
    gist.add_argument("--gist-id")
    gist.add_argument("--acknowledgement", default=ACKNOWLEDGEMENT)
    gist.set_defaults(handler=_command_publish_gist)
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
