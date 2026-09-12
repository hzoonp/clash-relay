from __future__ import annotations

import gzip

import pytest

from clash_relay.errors import FetchError
from clash_relay.fetch import _decompress_gzip_bounded


def test_gzip_decompression_is_bounded_by_expanded_size() -> None:
    compressed = gzip.compress(b"A" * 65_536)

    with pytest.raises(FetchError, match="decompressed subscription exceeds"):
        _decompress_gzip_bounded(compressed, 1024)


def test_gzip_decompression_accepts_payload_within_limit() -> None:
    payload = b"proxies: []\n"

    assert _decompress_gzip_bounded(gzip.compress(payload), len(payload)) == payload


def test_gzip_decompression_supports_concatenated_members() -> None:
    compressed = gzip.compress(b"first") + gzip.compress(b"second")

    assert _decompress_gzip_bounded(compressed, 11) == b"firstsecond"


def test_invalid_gzip_is_reported_as_fetch_error() -> None:
    with pytest.raises(FetchError, match="gzip payload is invalid"):
        _decompress_gzip_bounded(b"not-gzip", 1024)
