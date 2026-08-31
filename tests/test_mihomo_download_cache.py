from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


def _module(repo_root: Path):
    path = repo_root / "scripts" / "download_mihomo.py"
    spec = importlib.util.spec_from_file_location("download_mihomo_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verified_mihomo_cache_is_reused_without_network_metadata(repo_root: Path, tmp_path: Path) -> None:
    module = _module(repo_root)
    first_output = tmp_path / "mihomo-qualification"
    executable = b"verified-mihomo-binary"

    module._atomic_executable_write(first_output, executable)
    module._store_verified_cache(
        first_output,
        tag="v1.19.30",
        arch="linux-amd64",
        repository="MetaCubeX/mihomo",
        asset="mihomo-linux-amd64-v1.19.30.gz",
        compressed_sha256="a" * 64,
        executable=executable,
    )

    second_output = tmp_path / "mihomo-1.19.30"
    result = module._reuse_verified_cache(
        second_output,
        tag="v1.19.30",
        arch="linux-amd64",
        repository="MetaCubeX/mihomo",
    )

    assert result is not None
    assert result["cache_hit"] is True
    assert second_output.read_bytes() == executable
    assert second_output.stat().st_mode & 0o111


def test_tampered_mihomo_cache_is_never_reused(repo_root: Path, tmp_path: Path) -> None:
    module = _module(repo_root)
    output = tmp_path / "mihomo"
    executable_path, metadata_path = module._cache_paths(
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
        module._reuse_verified_cache(
            output,
            tag="v1.19.30",
            arch="linux-amd64",
            repository="MetaCubeX/mihomo",
        )
        is None
    )


def test_unverified_missing_digest_download_is_not_cached(repo_root: Path) -> None:
    text = (repo_root / "scripts" / "download_mihomo.py").read_text(encoding="utf-8")
    assert "if digest:\n        _store_verified_cache(" in text
    assert '"cache_hit": False' in text
