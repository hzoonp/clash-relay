"""Optional GitHub Gist publisher, intentionally outside the generator."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..errors import PublicationError


class GistPublisher:
    def __init__(self, *, token: str, gist_id: str) -> None:
        if not token or not gist_id:
            raise PublicationError("Gist token and ID are required")
        self._token = token
        self._gist_id = gist_id

    def publish(self, *, filename: str, content: str) -> str:
        body = json.dumps({"files": {filename: {"content": content}}}).encode("utf-8")
        request = urllib.request.Request(
            f"https://api.github.com/gists/{self._gist_id}",
            data=body,
            method="PATCH",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "User-Agent": "clash-relay/0.1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise PublicationError("Gist publication failed") from exc
        identifier = result.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise PublicationError("Gist API returned no ID")
        return identifier
