"""Private end-to-end verification of the fixed client subscription entry."""

from __future__ import annotations

import hashlib
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .errors import PublicationError
from .fetch import fetch_https_bytes, validate_subscription_url
from .mihomo import load_candidate, validate_with_mihomo

_PROPAGATION_DELAYS = (0, 2, 5, 10, 20, 30)
_MAX_BYTES = 16 * 1024 * 1024


def prepare_final_link_smoke(
    *, env: Mapping[str, str], binary: Path | None, content: bytes
) -> Callable[[], dict[str, Any]]:
    """Validate local prerequisites before any publication, then return a verifier.

    Only static failure categories escape this boundary. URL, response bytes,
    parser messages and core output (including exception chains) stay private.
    """
    url = env.get("CLASH_RELAY_PROFILE_URL", "")
    if not url:
        raise PublicationError("CLASH_RELAY_PROFILE_URL is required for final-link smoke")
    try:
        validate_subscription_url(url, allow_http=False, allow_file=False)
        if urlsplit(url).fragment:
            raise ValueError("fragment")
    except Exception:
        raise PublicationError("final-link smoke requires a valid HTTPS entry") from None
    if binary is None or not binary.is_file():
        raise PublicationError("final-link smoke requires the validated Mihomo binary")
    expected_digest = hashlib.sha256(content).digest()
    if not content or len(content) > _MAX_BYTES:
        raise PublicationError("final-link smoke candidate exceeds the supported byte bounds")

    def verify() -> dict[str, Any]:
        failure = "https_fetch"
        for attempt, delay in enumerate(_PROPAGATION_DELAYS, start=1):
            if delay:
                time.sleep(delay)
            try:
                fetched = fetch_https_bytes(url, timeout=15, max_bytes=_MAX_BYTES)
            except Exception:
                failure = "https_fetch"
                continue
            if hashlib.sha256(fetched).digest() != expected_digest:
                failure = "digest_mismatch"
                continue
            # Verify bytes before parsing/running a response from the public entry.
            try:
                with tempfile.TemporaryDirectory(prefix="clash-relay-entry-") as directory:
                    candidate = Path(directory) / "config.yaml"
                    candidate.write_bytes(fetched)
                    load_candidate(candidate)
                    validate_with_mihomo(binary, candidate)
            except Exception:
                raise PublicationError("final-link smoke failed: yaml_or_mihomo") from None
            return {
                "status": "passed",
                "https": "passed",
                "yaml": "passed",
                "mihomo": "passed",
                "digest": "matched",
                "attempts": attempt,
            }
        raise PublicationError(f"final-link smoke failed: {failure}") from None

    return verify
