"""Pinned official Mihomo download and verified local cache service."""

from __future__ import annotations

import contextlib
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

from .errors import ValidationError

_API_VERSION = "2022-11-28"
_CACHE_DIRECTORY = ".mihomo-cache"


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
                raise ValidationError("Mihomo release asset exceeds safety limit")
    return b"".join(chunks)


def default_architecture() -> str:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "linux-amd64"
    if machine in {"aarch64", "arm64"}:
        return "linux-arm64"
    raise ValidationError(f"unsupported host architecture: {machine}")


def _load_entry(
    manifest: Path, channel: str, tag: str | None
) -> tuple[str, dict[str, str], str]:
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
        entries = document[channel]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValidationError("failed to load pinned Mihomo release manifest") from exc
    if tag is None:
        entry = entries[0]
    else:
        matches = [item for item in entries if item["tag"] == tag]
        if not matches:
            raise ValidationError(f"tag {tag!r} is not pinned in {channel} manifest")
        entry = matches[0]
    return entry["tag"], entry["asset_patterns"], document["repository"]


def _atomic_executable_write(output: Path, content: bytes) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o755)
        os.replace(temporary, output)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def _cache_paths(output: Path, *, tag: str, arch: str) -> tuple[Path, Path]:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{tag}-{arch}")
    directory = output.parent / _CACHE_DIRECTORY
    return directory / safe, directory / f"{safe}.json"


def _reuse_verified_cache(
    output: Path,
    *,
    tag: str,
    arch: str,
    repository: str,
) -> dict[str, Any] | None:
    executable_path, metadata_path = _cache_paths(output, tag=tag, arch=arch)
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        executable = executable_path.read_bytes()
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(metadata, dict):
        return None
    expected = {"tag": tag, "arch": arch, "repository": repository}
    if any(metadata.get(key) != value for key, value in expected.items()):
        return None
    executable_sha256 = hashlib.sha256(executable).hexdigest()
    if metadata.get("executable_sha256") != executable_sha256:
        return None
    _atomic_executable_write(output, executable)
    return {
        "tag": tag,
        "asset": metadata.get("asset"),
        "compressed_sha256": metadata.get("compressed_sha256"),
        "digest_verified": True,
        "cache_hit": True,
        "output": str(output),
    }


def _store_verified_cache(
    output: Path,
    *,
    tag: str,
    arch: str,
    repository: str,
    asset: str,
    compressed_sha256: str,
    executable: bytes,
) -> None:
    executable_path, metadata_path = _cache_paths(output, tag=tag, arch=arch)
    executable_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_executable_write(executable_path, executable)
    metadata = {
        "tag": tag,
        "arch": arch,
        "repository": repository,
        "asset": asset,
        "compressed_sha256": compressed_sha256,
        "executable_sha256": hashlib.sha256(executable).hexdigest(),
    }
    fd, temporary = tempfile.mkstemp(prefix=f".{metadata_path.name}.", dir=metadata_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(metadata, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, metadata_path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def download_pinned_mihomo(
    *,
    manifest: Path,
    output: Path,
    channel: str = "stable",
    tag: str | None = None,
    arch: str | None = None,
    allow_missing_digest: bool = False,
) -> dict[str, Any]:
    """Prepare one pinned Mihomo binary without spawning a Python helper."""

    resolved_arch = arch or default_architecture()
    pinned_tag, patterns, repository = _load_entry(manifest, channel, tag)
    if resolved_arch not in patterns:
        raise ValidationError(f"architecture {resolved_arch!r} is not pinned for {pinned_tag}")

    cached = _reuse_verified_cache(
        output,
        tag=pinned_tag,
        arch=resolved_arch,
        repository=repository,
    )
    if cached is not None:
        return cached

    try:
        release = _request_json(
            f"https://api.github.com/repos/{repository}/releases/tags/{pinned_tag}"
        )
        if channel == "stable" and release.get("prerelease"):
            raise ValidationError(
                f"pinned stable tag {pinned_tag} is marked prerelease by GitHub"
            )
        pattern = re.compile(patterns[resolved_arch])
        assets = [asset for asset in release.get("assets", []) if pattern.fullmatch(asset["name"])]
        if len(assets) != 1:
            raise ValidationError(
                f"expected one Mihomo asset matching {pattern.pattern!r}, found {len(assets)}"
            )
        asset = assets[0]
        raw = _download(asset["browser_download_url"])
    except (OSError, urllib.error.URLError) as exc:
        raise ValidationError("failed to download pinned Mihomo release asset") from exc

    actual = hashlib.sha256(raw).hexdigest()
    digest = asset.get("digest")
    if digest:
        algorithm, _, expected = digest.partition(":")
        if algorithm.lower() != "sha256" or actual.lower() != expected.lower():
            raise ValidationError("Mihomo release asset digest verification failed")
    elif not allow_missing_digest:
        raise ValidationError("GitHub returned no digest for the pinned Mihomo asset")
    try:
        executable = gzip.decompress(raw) if asset["name"].endswith(".gz") else raw
    except OSError as exc:
        raise ValidationError("Mihomo release asset decompression failed") from exc
    _atomic_executable_write(output, executable)
    if digest:
        _store_verified_cache(
            output,
            tag=pinned_tag,
            arch=resolved_arch,
            repository=repository,
            asset=asset["name"],
            compressed_sha256=actual,
            executable=executable,
        )
    return {
        "tag": pinned_tag,
        "asset": asset["name"],
        "compressed_sha256": actual,
        "digest_verified": bool(digest),
        "cache_hit": False,
        "output": str(output),
    }
