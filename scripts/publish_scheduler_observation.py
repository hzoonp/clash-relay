#!/usr/bin/env python3
"""Publish the aggregate-only scheduler observation snapshot after production succeeds."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from clash_relay.config_loader import load_project
from clash_relay.scheduler_observation import publish_scheduler_observation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    root = args.root.resolve()
    project = load_project(
        config_path=root / "config.yaml",
        subscriptions_path=root / "subscriptions.yaml",
        policies_path=root / "policies.yaml",
    )
    result = publish_scheduler_observation(project=project, env=os.environ)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
