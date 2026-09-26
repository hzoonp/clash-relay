#!/usr/bin/env python3
"""Run one private carrier probe; write aggregate-only JSON."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

from clash_relay.builder import build_candidate
from clash_relay.carrier_probe import CARRIERS, run_carrier_probe
from clash_relay.carrier_qualification import run_carrier_qualification
from clash_relay.errors import ClashRelayError, ValidationError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--carrier", choices=sorted(CARRIERS), required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-targets", type=int, default=12)
    args = parser.parse_args(argv)
    try:
        # Gate before loading subscriptions or doing network I/O.
        from clash_relay.carrier_probe import assert_self_hosted_probe_environment

        assert_self_hosted_probe_environment(os.environ, args.carrier)
        key = os.environ.get("CLASH_RELAY_CARRIER_HMAC_KEY", "").encode()
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        if len(key) < 32 or not repository:
            raise ValidationError("carrier probe requires a repository-bound HMAC key")
        root = args.root.resolve()
        result = build_candidate(
            config_path=root / "config.yaml",
            subscriptions_path=root / "subscriptions.yaml",
            policies_path=root / "policies.yaml",
            env=os.environ,
        )
        candidate = yaml.safe_load(result.yaml_text)
        payload = run_carrier_probe(
            carrier=args.carrier,
            candidate=candidate,
            key=key,
            repository=repository,
            env=os.environ,
            max_targets=args.max_targets,
        )
        run_carrier_qualification(payload)
        destination = args.output.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        return 0
    except (ClashRelayError, OSError, ValueError):
        # Do not echo private inputs, source URLs, targets, or exception text.
        print("carrier probe failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
