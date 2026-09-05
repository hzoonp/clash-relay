from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from clash_relay import mihomo_download
from clash_relay.errors import ValidationError


def test_retryable_network_error_recovers_with_bounded_backoff(monkeypatch) -> None:
    attempts = 0
    sleeps: list[float] = []

    def flaky_request() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise urllib.error.URLError("temporary transport failure")
        return "ok"

    monkeypatch.setattr(mihomo_download.time, "sleep", sleeps.append)

    assert mihomo_download._retry_network_call(flaky_request) == "ok"
    assert attempts == 3
    assert sleeps == [1.0, 2.0]


def test_retryable_network_error_gives_up_after_three_attempts(monkeypatch) -> None:
    attempts = 0
    sleeps: list[float] = []

    def unavailable() -> None:
        nonlocal attempts
        attempts += 1
        raise urllib.error.URLError("still unavailable")

    monkeypatch.setattr(mihomo_download.time, "sleep", sleeps.append)

    with pytest.raises(urllib.error.URLError, match="still unavailable"):
        mihomo_download._retry_network_call(unavailable)

    assert attempts == 3
    assert sleeps == [1.0, 2.0]


def test_retryable_http_status_is_retried(monkeypatch) -> None:
    attempts = 0
    sleeps: list[float] = []

    def temporarily_unavailable() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise urllib.error.HTTPError(
                "https://example.invalid/mihomo",
                503,
                "service unavailable",
                None,
                None,
            )
        return "ok"

    monkeypatch.setattr(mihomo_download.time, "sleep", sleeps.append)

    assert mihomo_download._retry_network_call(temporarily_unavailable) == "ok"
    assert attempts == 2
    assert sleeps == [1.0]


def test_non_retryable_http_status_fails_immediately(monkeypatch) -> None:
    attempts = 0
    sleeps: list[float] = []

    def missing_asset() -> None:
        nonlocal attempts
        attempts += 1
        raise urllib.error.HTTPError(
            "https://example.invalid/mihomo",
            404,
            "not found",
            None,
            None,
        )

    monkeypatch.setattr(mihomo_download.time, "sleep", sleeps.append)

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        mihomo_download._retry_network_call(missing_asset)

    assert exc_info.value.code == 404
    assert attempts == 1
    assert sleeps == []


def test_validation_error_is_never_retried(monkeypatch) -> None:
    attempts = 0
    sleeps: list[float] = []

    def integrity_failure() -> None:
        nonlocal attempts
        attempts += 1
        raise ValidationError("integrity failure")

    monkeypatch.setattr(mihomo_download.time, "sleep", sleeps.append)

    with pytest.raises(ValidationError, match="integrity failure"):
        mihomo_download._retry_network_call(integrity_failure)

    assert attempts == 1
    assert sleeps == []


def test_digest_mismatch_fails_once_without_retrying_download(tmp_path: Path, monkeypatch) -> None:
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
    downloads = 0

    monkeypatch.setattr(
        mihomo_download,
        "_request_json",
        lambda url: {
            "prerelease": False,
            "assets": [
                {
                    "name": "mihomo-linux-amd64-v1.19.30",
                    "browser_download_url": "https://example.invalid/mihomo",
                    "digest": f"sha256:{'0' * 64}",
                }
            ],
        },
    )

    def download_once(url: str) -> bytes:
        nonlocal downloads
        downloads += 1
        return b"official-asset-bytes"

    monkeypatch.setattr(mihomo_download, "_download", download_once)

    with pytest.raises(ValidationError, match="digest verification failed"):
        mihomo_download.download_pinned_mihomo(
            manifest=manifest,
            output=output,
            tag="v1.19.30",
            arch="linux-amd64",
        )

    assert downloads == 1
    assert not output.exists()
