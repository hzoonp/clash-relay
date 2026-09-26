"""Print only aggregate health-check inventory from a private candidate file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from clash_relay.health_check_inventory import health_check_inventory
from clash_relay.util import yaml_load_no_aliases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        config = yaml_load_no_aliases(
            args.candidate.read_text(encoding="utf-8"), source="candidate"
        )
        if not isinstance(config, dict):
            raise ValueError("invalid candidate")
        report = health_check_inventory(config)
    except Exception:
        # Parser exceptions can contain private YAML excerpts or file paths.
        print(json.dumps({"status": "failed", "reason": "inventory_unavailable"}))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
