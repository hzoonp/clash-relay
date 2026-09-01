from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from clash_relay.errors import ClashRelayError, ValidationError
from clash_relay.mihomo import validate_with_mihomo
from clash_relay.mihomo_matrix import load_mihomo_tags


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate one private candidate against every pinned Mihomo core in a channel."
    )
    parser.add_argument("--candidate", type=_path, required=True)
    parser.add_argument("--manifest", type=_path, default=Path("tools/mihomo-versions.json"))
    parser.add_argument("--channel", default="stable")
    parser.add_argument("--work-dir", type=_path, required=True)
    parser.add_argument("--startup-seconds", type=float, default=1.5)
    return parser


def _download(tag: str, *, manifest: Path, work_dir: Path) -> Path:
    binary = work_dir / f"mihomo-{tag.removeprefix('v')}"
    command = [
        sys.executable,
        str(Path(__file__).with_name("download_mihomo.py")),
        "--manifest",
        str(manifest),
        "--channel",
        "stable",
        "--tag",
        tag,
        "--output",
        str(binary),
    ]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, encoding="utf-8")
    except OSError as exc:
        raise ValidationError(f"failed to start Mihomo downloader for {tag}") from exc
    if result.returncode != 0:
        raise ValidationError(f"failed to prepare pinned Mihomo core {tag}")
    return binary


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        tags = load_mihomo_tags(args.manifest, args.channel)
        args.work_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for tag in tags:
            binary = _download(tag, manifest=args.manifest, work_dir=args.work_dir)
            result = validate_with_mihomo(
                binary,
                args.candidate,
                startup_seconds=args.startup_seconds,
            )
            results.append({"tag": tag, "status": "passed", "mihomo": result})
        print(
            json.dumps(
                {
                    "status": "passed",
                    "channel": args.channel,
                    "validated_cores": list(tags),
                    "results": results,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except ClashRelayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
