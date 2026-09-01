from __future__ import annotations

import argparse
import json
from pathlib import Path

from clash_relay.mihomo_matrix import load_mihomo_tags


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a pinned Mihomo channel for CI matrices.")
    parser.add_argument("--manifest", type=Path, default=Path("tools/mihomo-versions.json"))
    parser.add_argument("--channel", default="stable")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    tags = list(load_mihomo_tags(args.manifest, args.channel))
    encoded = json.dumps(tags, separators=(",", ":"))
    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as handle:
            handle.write(f"tags={encoded}\n")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
