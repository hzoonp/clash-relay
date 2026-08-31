"""Fetch and validate the pinned ACL4SSR Online fidelity contract."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from .acl4ssr_reference import validate_acl4ssr_fidelity
from .errors import FetchError, GenerationError

ReferenceFetcher = Callable[..., str]


def _raw_url(repository: str, ref: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{repository}/{ref}/{quote(path, safe='/')}"


def validate_pinned_acl4ssr_reference(
    manifest: dict[str, Any] | None,
    *,
    fetcher: ReferenceFetcher,
    timeout: int,
) -> dict[str, Any] | None:
    """Fetch ``ACL4SSR_Online.ini`` from the same immutable ref and validate drift."""

    if manifest is None:
        return None
    contract = manifest.get("reference")
    if contract is None:
        return None
    if not isinstance(contract, dict) or not isinstance(contract.get("path"), str):
        raise GenerationError("ACL4SSR reference contract is malformed")
    path = str(contract["path"])
    url = _raw_url(str(manifest["repository"]), str(manifest["ref"]), path)
    try:
        text = fetcher(
            url,
            timeout=timeout,
            max_bytes=int(manifest["max_source_bytes"]),
            allow_http=False,
            allow_file=False,
        )
    except FetchError as exc:
        raise GenerationError("pinned ACL4SSR Online reference could not be fetched") from exc
    return validate_acl4ssr_fidelity(manifest, reference_text=text)
