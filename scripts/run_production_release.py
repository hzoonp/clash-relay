#!/usr/bin/env python3
"""Run the one canonical production lifecycle used by GitHub Actions and operators."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from clash_relay.config_loader import load_project
from clash_relay.errors import ClashRelayError, ValidationError
from clash_relay.production_diagnostics import safe_failure_diagnostic
from clash_relay.production_failure_metrics import persist_failure_diagnostic
from clash_relay.production_lifecycle import (
    ProductionLifecyclePaths,
    ProductionPipeline,
    resolve_publication_mode,
)
from clash_relay.scheduler_observation import publish_scheduler_observation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the canonical clash-relay production lifecycle."
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--publish", action="store_true", dest="publish")
    mode.add_argument("--dry-run", action="store_false", dest="publish")
    parser.set_defaults(publish=None)
    parser.add_argument("--event-name")
    parser.add_argument("--manual-publish")
    parser.add_argument("--workers", type=int, default=12)
    return parser


def _enforce_validated_ci_sha(*, publish: bool) -> None:
    if not publish or os.environ.get("GITHUB_ACTIONS", "").lower() != "true":
        return
    github_sha = os.environ.get("GITHUB_SHA", "").strip()
    validated_sha = os.environ.get("CLASH_RELAY_VALIDATED_SHA", "").strip()
    if not github_sha or not validated_sha or github_sha != validated_sha:
        raise ValidationError("CI publication requires the exact validated commit SHA")


def _publish_scheduler_observation(
    *, root: Path, publish: bool, lifecycle_result: dict[str, Any]
) -> dict[str, Any]:
    """Publish scheduler evidence only after a successful persistent release.

    This keeps every persistent production write behind the canonical production
    entrypoint and, critically, makes a manual dry-run side-effect free.
    """

    if not publish or lifecycle_result.get("publication_status") != "published":
        return {"status": "skipped", "reason": "dry_run"}
    if lifecycle_result.get("status") != "passed":
        return {"status": "skipped", "reason": "release_not_passed"}

    project = load_project(
        config_path=root / "config.yaml",
        subscriptions_path=root / "subscriptions.yaml",
        policies_path=root / "policies.yaml",
    )
    return publish_scheduler_observation(project=project, env=os.environ)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    publish = False
    try:
        publish = resolve_publication_mode(
            explicit_publish=args.publish,
            event_name=args.event_name or os.environ.get("GITHUB_EVENT_NAME"),
            manual_publish=(
                args.manual_publish
                if args.manual_publish is not None
                else os.environ.get("CLASH_RELAY_MANUAL_PUBLISH")
            ),
        )
        _enforce_validated_ci_sha(publish=publish)
        result = ProductionPipeline(
            ProductionLifecyclePaths.canonical(args.root),
            publish=publish,
            workers=args.workers,
        ).run()
        observation = _publish_scheduler_observation(
            root=args.root.resolve(),
            publish=publish,
            lifecycle_result=result,
        )
        result["scheduler_observation"] = observation.get("status", "unknown")
        if publish and observation.get("status") == "unavailable":
            print(
                "::warning title=Scheduler observation::Production release is valid, "
                "but the aggregate scheduler observation could not be published."
            )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ClashRelayError) as exc:
        diagnostic = safe_failure_diagnostic(exc)
        if publish:
            persist_failure_diagnostic(root=args.root, diagnostic=diagnostic, env=os.environ)
        print(json.dumps(diagnostic, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
