"""Validate one private candidate against the pinned Mihomo matrix in process."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import ValidationError
from .mihomo import validate_with_mihomo
from .mihomo_download import download_pinned_mihomo
from .mihomo_matrix import load_mihomo_tags


def validate_mihomo_matrix(
    *,
    candidate: Path,
    manifest: Path,
    channel: str,
    work_dir: Path,
    reuse_primary_bin: Path | None = None,
    startup_seconds: float = 1.5,
) -> dict[str, Any]:
    """Run the pinned validation matrix without launching Python helper scripts."""

    tags = load_mihomo_tags(manifest, channel)
    work_dir.mkdir(parents=True, exist_ok=True)
    if reuse_primary_bin is not None and not reuse_primary_bin.is_file():
        raise ValidationError("reused primary Mihomo binary does not exist")

    results: list[dict[str, Any]] = []
    downloaded = 0
    for index, tag in enumerate(tags):
        reuse_primary = index == 0 and reuse_primary_bin is not None
        if reuse_primary:
            binary = reuse_primary_bin
        else:
            binary = work_dir / f"mihomo-{tag.removeprefix('v')}"
            download_pinned_mihomo(
                manifest=manifest,
                channel=channel,
                tag=tag,
                output=binary,
            )
            downloaded += 1
        if binary is None:  # pragma: no cover - defensive invariant
            raise ValidationError("failed to resolve Mihomo validation binary")
        validation = validate_with_mihomo(
            binary,
            candidate,
            startup_seconds=startup_seconds,
        )
        results.append(
            {
                "tag": tag,
                "status": "passed",
                "reused_primary": reuse_primary,
                "mihomo": validation,
            }
        )

    return {
        "status": "passed",
        "channel": channel,
        "validated_cores": list(tags),
        "reused_primary": reuse_primary_bin is not None,
        "downloaded_cores": downloaded,
        "results": results,
    }
