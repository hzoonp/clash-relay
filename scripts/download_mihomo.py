#!/usr/bin/env python3
"""Download a pinned official Mihomo release asset and verify its GitHub digest."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
import re
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


_API_VERSION = "2022-11-28"


def _request_json(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "clash-relay-mihomo-downloader/0.1",
            "X-GitHub-Api-Version": _API_VERSION,
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def _download(url: str, maximum: int = 128 * 1024 * 1024) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "clash-relay-mihomo-downloader/0.1",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise RuntimeError("Mihomo release asset exceeds safety limit")
    return b"".join(chunks)


def _default_arch() -> str:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "linux-amd64"
    if machine in {"aarch64", "arm64"}:
        return "linux-arm64"
    raise RuntimeError(f"unsupported host architecture: {machine}")


def _load_entry(manifest: Path, channel: str, tag: str | None) -> tuple[str, dict[str, str], str]:
    document = json.loads(manifest.read_text(encoding="utf-8"))
    entries = document[channel]
    if tag is None:
        entry = entries[0]
    else:
        matches = [item for item in entries if item["tag"] == tag]
        if not matches:
            raise RuntimeError(f"tag {tag!r} is not pinned in {channel} manifest")
        entry = matches[0]
    return entry["tag"], entry["asset_patterns"], document["repository"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("tools/mihomo-versions.json"))
    parser.add_argument("--channel", choices=["stable", "prerelease"], default="stable")
    parser.add_argument("--tag")
    parser.add_argument("--arch", default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--allow-missing-digest",
        action="store_true",
        help="Not recommended. Permit an asset when GitHub returns no sha256 digest.",
    )
    args = parser.parse_args()
    arch = args.arch or _default_arch()
    tag, patterns, repository = _load_entry(args.manifest, args.channel, args.tag)
    if arch not in patterns:
        raise RuntimeError(f"architecture {arch!r} is not pinned for {tag}")
    release = _request_json(f"https://api.github.com/repos/{repository}/releases/tags/{tag}")
    if args.channel == "stable" and release.get("prerelease"):
        raise RuntimeError(f"pinned stable tag {tag} is marked prerelease by GitHub")
    pattern = re.compile(patterns[arch])
    assets = [asset for asset in release.get("assets", []) if pattern.fullmatch(asset["name"])]
    if len(assets) != 1:
        names = [asset.get("name") for asset in release.get("assets", [])]
        raise RuntimeError(
            f"expected one asset matching {pattern.pattern!r}, found {len(assets)}; assets={names}"
        )
    asset = assets[0]
    raw = _download(asset["browser_download_url"])
    actual = hashlib.sha256(raw).hexdigest()
    digest = asset.get("digest")
    if digest:
        algorithm, _, expected = digest.partition(":")
        if algorithm.lower() != "sha256" or actual.lower() != expected.lower():
            raise RuntimeError("Mihomo release asset digest verification failed")
    elif not args.allow_missing_digest:
        raise RuntimeError("GitHub returned no digest for the pinned Mihomo asset")
    try:
        executable = gzip.decompress(raw) if asset["name"].endswith(".gz") else raw
    except OSError as exc:
        raise RuntimeError("Mihomo release asset decompression failed") from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{args.output.name}.", dir=args.output.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(executable)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o755)
        os.replace(temporary, args.output)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    print(
        json.dumps(
            {
                "tag": tag,
                "asset": asset["name"],
                "compressed_sha256": actual,
                "digest_verified": bool(digest),
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        raise SystemExit(f"error: {exc}") from exc
