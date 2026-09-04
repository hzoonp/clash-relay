from __future__ import annotations

import hashlib
import json
from pathlib import Path

from clash_relay import mihomo_download


def test_verified_mihomo_cache_is_reused_without_network_metadata(tmp_path: Path) -> None:
    first_output = tmp_path / "mihomo-qualification"
    executable = b"verified-mihomo-binary"

    mihomo_download._atomic_executable_write(first_output, executable)
    mihomo_download._store_verified_cache(
        first_output,
        tag="v1.19.30",
        arch="linux-amd64",
        repository="MetaCubeX/mihomo",
        asset="mihomo-linux-amd64-v1.19.30.gz",
        compressed_sha256="a" * 64,
        executable=executable,
    )

    second_output = tmp_path / "mihomo-1.19.30"
    result = mihomo_download._reuse_verified_cache(
        second_output,
        tag="v1.19.30",
        arch="linux-amd64",
        repository="MetaCubeX/mihomo",
    )

    assert result is not None
    assert result["cache_hit"] is True
    assert second_output.read_bytes() == executable
    assert second_output.stat().st_mode & 0o111


def test_tampered_mihomo_cache_is_never_reused(tmp_path: Path) -> None:
    output = tmp_path / "mihomo"
    executable_path, metadata_path = mihomo_download._cache_paths(
        output, tag="v1.19.30", arch="linux-amd64"
    )
    executable_path.parent.mkdir(parents=True)
    executable_path.write_bytes(b"tampered")
    metadata_path.write_text(
        json.dumps(
            {
                "tag": "v1.19.30",
                "arch": "linux-amd64",
                "repository": "MetaCubeX/mihomo",
                "asset": "mihomo-linux-amd64-v1.19.30.gz",
                "compressed_sha256": "a" * 64,
                "executable_sha256": hashlib.sha256(b"original").hexdigest(),
            }
        ),
        encoding="utf-8",
    )

    assert (
        mihomo_download._reuse_verified_cache(
            output,
            tag="v1.19.30",
            arch="linux-amd64",
            repository="MetaCubeX/mihomo",
        )
        is None
    )


def test_unverified_missing_digest_download_is_not_cached(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "mihomo-versions.json"
    manifest.write_text(
        json.dumps(
            {
                "repository": "MetaCubeX/mihomo",
                "stable": [
                    {
                        "tag": "v1.19.30",
                        "asset_patterns": {"linux-amd64": "mihomo-linux-amd64-v1\\.19\\.30"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "mihomo"
    executable = b"unverified-mihomo-binary"
    monkeypatch.setattr(
        mihomo_download,
        "_request_json",
        lambda url: {
            "prerelease": False,
            "assets": [
                {
                    "name": "mihomo-linux-amd64-v1.19.30",
                    "browser_download_url": "https://example.invalid/mihomo",
                }
            ],
        },
    )
    monkeypatch.setattr(mihomo_download, "_download", lambda url: executable)

    def reject_cache_store(*args, **kwargs):
        raise AssertionError("unverified downloads must not enter the verified cache")

    monkeypatch.setattr(mihomo_download, "_store_verified_cache", reject_cache_store)

    result = mihomo_download.download_pinned_mihomo(
        manifest=manifest,
        output=output,
        tag="v1.19.30",
        arch="linux-amd64",
        allow_missing_digest=True,
    )

    assert result["digest_verified"] is False
    assert result["cache_hit"] is False
    assert output.read_bytes() == executable
