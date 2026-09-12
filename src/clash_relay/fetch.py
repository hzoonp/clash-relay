"""Bounded subscription fetching with scheme and destination checks."""

from __future__ import annotations

import gzip
import http.client
import io
import ipaddress
import socket
import ssl
import urllib.error
import urllib.request
import zlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any
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


def _resolve_public_destination(url: str) -> tuple[tuple[Any, ...], ...]:
    """Resolve one URL and return only the addresses this connection may use."""
    parsed = urlsplit(url)
    if parsed.scheme == "file":
        return ()
    hostname = parsed.hostname
    if not hostname:
        raise FetchError("subscription URL has no hostname")
    lowered = hostname.lower().rstrip(".")
    if lowered == "localhost" or lowered.endswith(".localhost"):
        raise FetchError("subscription hostname may not target localhost")
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        answers = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(
            f"subscription hostname could not be resolved for {redact_url(url)}"
        ) from exc

    usable: list[tuple[Any, ...]] = []
    seen: set[tuple[Any, ...]] = set()
    for answer in answers:
        if not answer or len(answer) < 5 or not answer[4]:
            continue
        address = str(answer[4][0])
        if _is_private_literal(address):
            raise FetchError("subscription hostname resolves to a private or special-use address")
        normalized = tuple(answer)
        if normalized not in seen:
            seen.add(normalized)
            usable.append(normalized)
    if not usable:
        raise FetchError("subscription hostname resolved to no usable address")
    return tuple(usable)


def _validate_resolved_destination(url: str) -> None:
    """Reject hostnames whose current DNS answers include private/special-use addresses."""
    _resolve_public_destination(url)


def _connect_resolved(
    answers: Iterable[tuple[Any, ...]],
    *,
    timeout: float | object,
    source_address: tuple[str, int] | None,
) -> socket.socket:
    """Connect only to a previously validated getaddrinfo result set."""
    last_error: OSError | None = None
    for family, socktype, proto, _canonname, sockaddr in answers:
        sock: socket.socket | None = None
        try:
            sock = socket.socket(family, socktype, proto)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)  # type: ignore[arg-type]
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_error = exc
            if sock is not None:
                sock.close()
    if last_error is not None:
        raise last_error
    raise OSError("no validated subscription destination is available")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, resolved_answers: tuple[tuple[Any, ...], ...], **kwargs) -> None:
        self._resolved_answers = resolved_answers
        super().__init__(*args, **kwargs)

    def connect(self) -> None:
        self.sock = _connect_resolved(
            self._resolved_answers,
            timeout=self.timeout,
            source_address=self.source_address,
        )
        if self._tunnel_host:
            self._tunnel()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, resolved_answers: tuple[tuple[Any, ...], ...], **kwargs) -> None:
        self._resolved_answers = resolved_answers
        super().__init__(*args, **kwargs)

    def connect(self) -> None:
        self.sock = _connect_resolved(
            self._resolved_answers,
            timeout=self.timeout,
            source_address=self.source_address,
        )
        server_hostname = self.host
        if self._tunnel_host:
            self._tunnel()
            server_hostname = self._tunnel_host
        self.sock = self._context.wrap_socket(self.sock, server_hostname=server_hostname)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        answers = _resolve_public_destination(req.full_url)

        def connection(host, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, **kwargs):
            return _PinnedHTTPConnection(
                host,
                timeout=timeout,
                resolved_answers=answers,
                **kwargs,
            )

        return self.do_open(connection, req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        answers = _resolve_public_destination(req.full_url)

        def connection(host, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, **kwargs):
            return _PinnedHTTPSConnection(
                host,
                timeout=timeout,
                resolved_answers=answers,
                **kwargs,
            )

        return self.do_open(connection, req, context=self._context)


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


def _read_bounded(response, max_bytes: int, *, limit_error: str | None = None) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(65536, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise FetchError(limit_error or "subscription exceeds the configured byte limit")
    return b"".join(chunks)


def _decompress_gzip_bounded(raw: bytes, max_bytes: int) -> bytes:
    """Decompress gzip data without allocating beyond the expanded byte budget."""
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as stream:
            return _read_bounded(
                stream,
                max_bytes,
                limit_error="decompressed subscription exceeds the byte limit",
            )
    except FetchError:
        raise
    except (OSError, EOFError, zlib.error) as exc:
        raise FetchError("subscription gzip payload is invalid") from exc


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
            # Subscription fetches must never inherit ambient HTTP(S) proxy
            # settings: a proxy would break the validated-DNS-to-socket binding.
            urllib.request.ProxyHandler({}),
            _SafeRedirectHandler(allow_http=allow_http, allow_file=allow_file),
            _PinnedHTTPHandler(),
            _PinnedHTTPSHandler(context=context),
        )
        try:
            with opener.open(request, timeout=timeout) as response:
                validate_subscription_url(
                    response.geturl(), allow_http=allow_http, allow_file=allow_file
                )
                _validate_resolved_destination(response.geturl())
                raw = _read_bounded(response, max_bytes)
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    raw = _decompress_gzip_bounded(raw, max_bytes)
        except FetchError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            safe = redact_text(str(exc), [url])
            raise FetchError(f"subscription fetch failed for {redact_url(url)}: {safe}") from exc
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FetchError("subscription is not valid UTF-8") from exc
