#!/usr/bin/env python3
"""Compare a qualified candidate with current production before activation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clash_relay.config_loader import load_project
from clash_relay.errors import ClashRelayError, ValidationError
from clash_relay.mihomo import load_candidate
from clash_relay.promotion_guard import assess_promotion, load_promotion_guard_policy
from clash_relay.util import atomic_write


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--subscriptions", type=Path, default=Path("subscriptions.yaml"))
    parser.add_argument("--services", type=Path, default=Path("services.yaml"))
    parser.add_argument("--policies", type=Path, default=Path("policies.yaml"))
    parser.add_argument("--guard", type=Path, default=Path("promotion-guard.yaml"))
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--markdown", type=Path)
    return parser


def _markdown(report: dict) -> str:
    candidate = report.get("candidate", {})
    baseline = report.get("baseline", {})
    ratios = report.get("ratios", {})
    violations = report.get("violations", [])
    lines = [
        "## Production promotion guard",
        "",
        f"Decision: **{report.get('status', 'unknown')}**  ",
        f"Reason: **{report.get('reason', 'unknown')}**",
        "",
    ]
    if isinstance(candidate, dict):
        lines.append(
            f"Candidate inventory: **{int(candidate.get('nodes', 0))} nodes / {int(candidate.get('providers', 0))} providers**  "
        )
    if isinstance(baseline, dict):
        lines.append(
            f"Production baseline: **{int(baseline.get('nodes', 0))} nodes / {int(baseline.get('providers', 0))} providers**  "
        )
    if isinstance(ratios, dict) and ratios:
        lines.append(
            f"Candidate/baseline ratios: **nodes {ratios.get('total_nodes', 'n/a')} / providers {ratios.get('providers', 'n/a')}**"
        )
    if violations:
        lines.extend(["", "Blocked checks: **" + ", ".join(str(item) for item in violations) + "**"])
    lines.extend(
        [
            "",
            "Only aggregate inventory counts are emitted; node names, servers, and credentials remain private.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        project = load_project(
            config_path=args.config,
            subscriptions_path=args.subscriptions,
            services_path=args.services,
            policies_path=args.policies,
        )
        candidate = load_candidate(args.candidate)
        baseline = load_candidate(args.baseline) if args.baseline.is_file() else None
        policy = load_promotion_guard_policy(args.guard)
        report = assess_promotion(project, candidate, baseline, policy)
        text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.report is not None:
            atomic_write(args.report, text)
        if args.markdown is not None:
            atomic_write(args.markdown, _markdown(report))
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        if report.get("status") == "blocked":
            return 2
        if report.get("status") != "passed":
            raise ValidationError("promotion guard returned an invalid decision")
        return 0
    except ClashRelayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
