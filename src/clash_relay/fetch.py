"""Bounded subscription fetching with scheme and destination checks."""

from __future__ import annotations

import gzip
import ipaddress
import socket
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .errors import FetchError
from .redact import redact_text, redact_url

_USER_AGENT = "clash-relay/0.1 (+https://github.com/)"


def _is_private_literal(hostname: str) -> bool:
    try:
        address = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        return False
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def validate_subscription_url(
    url: str,
    *,
    allow_http: bool,
    allow_file: bool,
) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise FetchError("subscription URL is malformed") from exc
    if parsed.username is not None or parsed.password is not None:
        raise FetchError("subscription URL userinfo is not allowed")
    allowed = {"https"}
    if allow_http:
        allowed.add("http")
    if allow_file:
        allowed.add("file")
    if parsed.scheme.lower() not in allowed:
        raise FetchError(f"subscription URL scheme {parsed.scheme!r} is not allowed")
    if parsed.scheme == "file":
        if parsed.netloc not in {"", "localhost"}:
            raise FetchError("file subscription URL must be local")
        if not parsed.path:
            raise FetchError("file subscription URL has no path")
        return
    if not parsed.hostname:
        raise FetchError("subscription URL has no hostname")
    if port is not None and not 1 <= port <= 65535:
        raise FetchError("subscription URL has an invalid port")
    if _is_private_literal(parsed.hostname):
        raise FetchError("subscription URL may not target a private or special-use IP literal")


def _validate_resolved_destination(url: str) -> None:
    """Reject hostnames whose current DNS answers include private/special-use addresses."""
    parsed = urlsplit(url)
    if parsed.scheme == "file":
        return
    hostname = parsed.hostname
    if not hostname:
        raise FetchError("subscription URL has no hostname")
    if hostname.lower() == "localhost" or hostname.lower().endswith(".localhost"):
        raise FetchError("subscription hostname may not target localhost")
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        answers = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(
            f"subscription hostname could not be resolved for {redact_url(url)}"
        ) from exc
    addresses = {
        str(answer[4][0]) for answer in answers if answer and len(answer) >= 5 and answer[4]
    }
    if not addresses:
        raise FetchError("subscription hostname resolved to no usable address")
    if any(_is_private_literal(address) for address in addresses):
        raise FetchError("subscription hostname resolves to a private or special-use address")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, *, allow_http: bool, allow_file: bool) -> None:
        self._allow_http = allow_http
        self._allow_file = allow_file
        super().__init__()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_subscription_url(
            newurl,
            allow_http=self._allow_http,
            allow_file=self._allow_file,
        )
        _validate_resolved_destination(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_bounded(response, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(65536, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise FetchError("subscription exceeds the configured byte limit")
    return b"".join(chunks)


def fetch_subscription(
    url: str,
    *,
    timeout: int,
    max_bytes: int,
    allow_http: bool,
    allow_file: bool,
) -> str:
    validate_subscription_url(url, allow_http=allow_http, allow_file=allow_file)
    parsed = urlsplit(url)
    if parsed.scheme == "file":
        path = Path(unquote(parsed.path))
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise FetchError("cannot read local subscription fixture") from exc
        if len(raw) > max_bytes:
            raise FetchError("subscription exceeds the configured byte limit")
    else:
        _validate_resolved_destination(url)
        request = urllib.request.Request(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept-Encoding": "gzip"},
            method="GET",
        )
        context = ssl.create_default_context()
        opener = urllib.request.build_opener(
            _SafeRedirectHandler(allow_http=allow_http, allow_file=allow_file),
            urllib.request.HTTPSHandler(context=context),
        )
        try:
            with opener.open(request, timeout=timeout) as response:
                validate_subscription_url(
                    response.geturl(), allow_http=allow_http, allow_file=allow_file
                )
                _validate_resolved_destination(response.geturl())
                raw = _read_bounded(response, max_bytes)
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    try:
                        raw = gzip.decompress(raw)
                    except (OSError, EOFError) as exc:
                        raise FetchError("subscription gzip payload is invalid") from exc
                    if len(raw) > max_bytes:
                        raise FetchError("decompressed subscription exceeds the byte limit")
        except FetchError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            safe = redact_text(str(exc), [url])
            raise FetchError(f"subscription fetch failed for {redact_url(url)}: {safe}") from exc
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FetchError("subscription is not valid UTF-8") from exc
