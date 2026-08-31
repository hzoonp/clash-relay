"""Publish validated credential-bearing configuration to private Cloudflare Workers KV."""

from __future__ import annotations

import hashlib
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ..errors import PublicationError

_API_ROOT = "https://api.cloudflare.com/client/v4"
_MAX_VALUE_BYTES = 25 * 1024 * 1024
_MAX_NAMESPACE_PAGES = 100


def _request_json(request: urllib.request.Request) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        raise PublicationError(f"Cloudflare API request failed with HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise PublicationError("Cloudflare API request failed") from exc
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicationError("Cloudflare API returned an invalid response") from exc
    if not isinstance(document, dict) or document.get("success") is not True:
        raise PublicationError("Cloudflare API rejected the request")
    return document


def _validate_key_name(key_name: str) -> None:
    if not key_name or key_name in {".", ".."}:
        raise PublicationError("Cloudflare KV key must be a non-empty ordinary key")
    if any(character.isspace() for character in key_name):
        raise PublicationError("Cloudflare KV key must not contain whitespace")
    if len(key_name.encode()) > 512:
        raise PublicationError("Cloudflare KV key exceeds the 512-byte limit")


class CloudflareKVPublisher:
    def __init__(
        self,
        *,
        token: str,
        account_id: str,
        namespace_title: str,
        key_name: str = "production-config",
    ) -> None:
        if not token:
            raise PublicationError("Cloudflare API token is required")
        if not account_id:
            raise PublicationError("Cloudflare account ID is required")
        if not namespace_title.strip():
            raise PublicationError("Cloudflare KV namespace title is required")
        _validate_key_name(key_name)
        self._token = token
        self._account_id = account_id
        self._namespace_title = namespace_title
        self._key_name = key_name

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "clash-relay/0.1",
        }

    def _namespace_id(self) -> str:
        matches: list[str] = []
        encoded_account = urllib.parse.quote(self._account_id, safe="")
        for page in range(1, _MAX_NAMESPACE_PAGES + 1):
            query = urllib.parse.urlencode({"page": page, "per_page": 100, "direction": "asc"})
            request = urllib.request.Request(
                f"{_API_ROOT}/accounts/{encoded_account}/storage/kv/namespaces?{query}",
                headers=self._headers(),
                method="GET",
            )
            document = _request_json(request)
            items = document.get("result")
            if not isinstance(items, list):
                raise PublicationError("Cloudflare namespace listing returned an invalid result")
            for item in items:
                if not isinstance(item, dict) or item.get("title") != self._namespace_title:
                    continue
                identifier = item.get("id")
                if isinstance(identifier, str) and identifier:
                    matches.append(identifier)
            result_info = document.get("result_info")
            total_count = result_info.get("total_count") if isinstance(result_info, dict) else None
            if isinstance(total_count, int) and page * 100 >= total_count:
                break
            if len(items) < 100:
                break
        else:
            raise PublicationError("Cloudflare namespace listing exceeded the safety page limit")
        if len(matches) != 1:
            raise PublicationError(
                "Cloudflare KV namespace title must resolve to exactly one namespace"
            )
        return matches[0]

    def _value_url(self, namespace_id: str) -> str:
        encoded_account = urllib.parse.quote(self._account_id, safe="")
        encoded_namespace = urllib.parse.quote(namespace_id, safe="")
        encoded_key = urllib.parse.quote(self._key_name, safe="")
        return (
            f"{_API_ROOT}/accounts/{encoded_account}/storage/kv/namespaces/"
            f"{encoded_namespace}/values/{encoded_key}"
        )

    def read(self) -> bytes | None:
        """Read the exact value bytes; a missing key is represented as None."""
        namespace_id = self._namespace_id()
        request = urllib.request.Request(
            self._value_url(namespace_id),
            headers=self._headers(),
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                content = response.read(_MAX_VALUE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise PublicationError(f"Cloudflare API request failed with HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise PublicationError("Cloudflare API request failed") from exc
        if len(content) > _MAX_VALUE_BYTES:
            raise PublicationError("Cloudflare KV value exceeds the 25 MiB safety limit")
        return content

    def publish(self, *, content: bytes) -> dict[str, Any]:
        if not content:
            raise PublicationError("refusing to publish an empty Cloudflare KV value")
        if len(content) > _MAX_VALUE_BYTES:
            raise PublicationError("generated config exceeds Cloudflare KV's 25 MiB value limit")

        namespace_id = self._namespace_id()
        boundary = f"clash-relay-{secrets.token_hex(16)}"
        prefix = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="value"; filename="value.bin"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        suffix = f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(
            self._value_url(namespace_id),
            data=prefix + content + suffix,
            method="PUT",
            headers={
                **self._headers(),
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        _request_json(request)
        return {
            "backend": "cloudflare_kv",
            "namespace_title": self._namespace_title,
            "key": self._key_name,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
