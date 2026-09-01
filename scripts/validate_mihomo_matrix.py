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
    parser.add_argument("--reuse-primary-bin", type=_path)
    parser.add_argument("--startup-seconds", type=float, default=1.5)
    return parser


def _download(tag: str, *, manifest: Path, channel: str, work_dir: Path) -> Path:
    binary = work_dir / f"mihomo-{tag.removeprefix('v')}"
    command = [
        sys.executable,
        str(Path(__file__).with_name("download_mihomo.py")),
        "--manifest",
        str(manifest),
        "--channel",
        channel,
        "--tag",
        tag,
        "--output",
        str(binary),
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
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
        if args.reuse_primary_bin is not None and not args.reuse_primary_bin.is_file():
            raise ValidationError("reused primary Mihomo binary does not exist")
        results = []
        for index, tag in enumerate(tags):
            reuse_primary = index == 0 and args.reuse_primary_bin is not None
            binary = (
                args.reuse_primary_bin
                if reuse_primary
                else _download(
                    tag,
                    manifest=args.manifest,
                    channel=args.channel,
                    work_dir=args.work_dir,
                )
            )
            if binary is None:
                raise ValidationError("failed to resolve Mihomo validation binary")
            result = validate_with_mihomo(
                binary,
                args.candidate,
                startup_seconds=args.startup_seconds,
            )
            results.append(
                {
                    "tag": tag,
                    "status": "passed",
                    "reused_primary": reuse_primary,
                    "mihomo": result,
                }
            )
        print(
            json.dumps(
                {
                    "status": "passed",
                    "channel": args.channel,
                    "validated_cores": list(tags),
                    "reused_primary": args.reuse_primary_bin is not None,
                    "downloaded_cores": len(tags)
                    - (1 if args.reuse_primary_bin is not None else 0),
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
