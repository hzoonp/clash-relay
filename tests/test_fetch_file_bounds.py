from __future__ import annotations

import pytest

from clash_relay.errors import FetchError
from clash_relay.fetch import fetch_subscription


def test_local_subscription_stops_after_byte_limit(tmp_path, monkeypatch) -> None:
    path = tmp_path / "large.txt"
    path.write_bytes(b"x" * 100_000)

    def unexpected_read_bytes(self):
        raise AssertionError("whole-file read bypassed the byte limit")

    monkeypatch.setattr(type(path), "read_bytes", unexpected_read_bytes)

    with pytest.raises(FetchError, match="configured byte limit"):
        fetch_subscription(path.as_uri(), timeout=2, max_bytes=8, allow_http=False, allow_file=True)
