"""Publication backend protocol."""

from __future__ import annotations

from typing import Protocol


class Publisher(Protocol):
    def publish(self, *, filename: str, content: str) -> str:
        """Publish content and return a non-secret resource identifier."""
