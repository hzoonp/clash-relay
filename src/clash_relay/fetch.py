"""Bounded subscription fetching with scheme and destination checks."""

from __future__ import annotations

import gzip
import http.client
import io
import ipaddress
import socket
import ssl
import time
import urllib.error
import urllib.request
import zlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

from .errors import FetchError
from .redact import redact_text, redact_url

_USER_AGENT = "clash-relay/0.1 (+https://github.com/)"
_CLIENT_USER_AGENTS = {
    "default": _USER_AGENT,
    "mihomo": "clash.meta",
}


class _Deadline:
    """One monotonic deadline shared by every operation for one subscription."""

    def __init__(self, timeout: float) -> None:
        self._end = time.monotonic() + timeout

    def remaining(self) -> float:
        remaining = self._end - time.monotonic()
        if remaining <= 0:
            raise FetchError("subscription fetch exceeded the configured total timeout")
        return remaining


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


def _resolve_public_destination(
    url: str, *, deadline: _Deadline | None = None
) -> tuple[tuple[Any, ...], ...]:
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
        if deadline is not None:
            deadline.remaining()
        answers = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        if deadline is not None:
            deadline.remaining()
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


def _validate_resolved_destination(url: str, *, deadline: _Deadline | None = None) -> None:
    """Reject hostnames whose current DNS answers include private/special-use addresses."""
    _resolve_public_destination(url, deadline=deadline)


def _connect_resolved(
    answers: Iterable[tuple[Any, ...]],
    *,
    timeout: float | object,
    source_address: tuple[str, int] | None,
    deadline: _Deadline | None = None,
) -> socket.socket:
    """Connect only to a previously validated getaddrinfo result set."""
    last_error: OSError | None = None
    for family, socktype, proto, _canonname, sockaddr in answers:
        sock: socket.socket | None = None
        try:
            sock = socket.socket(family, socktype, proto)
            timeout_seconds = deadline.remaining() if deadline is not None else timeout
            if timeout_seconds is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout_seconds)  # type: ignore[arg-type]
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
    def __init__(
        self, *args, resolved_answers: tuple[tuple[Any, ...], ...], deadline: _Deadline, **kwargs
    ) -> None:
        self._resolved_answers = resolved_answers
        self._deadline = deadline
        super().__init__(*args, **kwargs)

    def connect(self) -> None:
        self.sock = _connect_resolved(
            self._resolved_answers,
            timeout=self.timeout,
            source_address=self.source_address,
            deadline=self._deadline,
        )
        if self._tunnel_host:
            self._tunnel()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self, *args, resolved_answers: tuple[tuple[Any, ...], ...], deadline: _Deadline, **kwargs
    ) -> None:
        self._resolved_answers = resolved_answers
        self._deadline = deadline
        super().__init__(*args, **kwargs)

    def connect(self) -> None:
        self.sock = _connect_resolved(
            self._resolved_answers,
            timeout=self.timeout,
            source_address=self.source_address,
            deadline=self._deadline,
        )
        server_hostname = self.host
        if self._tunnel_host:
            self._tunnel()
            server_hostname = self._tunnel_host
        self.sock = self._context.wrap_socket(self.sock, server_hostname=server_hostname)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, *, deadline: _Deadline) -> None:
        self._deadline = deadline
        super().__init__()

    def http_open(self, req):
        answers = _resolve_public_destination(req.full_url, deadline=self._deadline)

        def connection(host, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, **kwargs):
            return _PinnedHTTPConnection(
                host,
                timeout=timeout,
                resolved_answers=answers,
                deadline=self._deadline,
                **kwargs,
            )

        return self.do_open(connection, req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, *, context: ssl.SSLContext, deadline: _Deadline) -> None:
        self._deadline = deadline
        super().__init__(context=context)

    def https_open(self, req):
        answers = _resolve_public_destination(req.full_url, deadline=self._deadline)

        def connection(host, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, **kwargs):
            return _PinnedHTTPSConnection(
                host,
                timeout=timeout,
                resolved_answers=answers,
                deadline=self._deadline,
                **kwargs,
            )

        return self.do_open(connection, req, context=self._context)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, *, allow_http: bool, allow_file: bool, deadline: _Deadline) -> None:
        self._allow_http = allow_http
        self._allow_file = allow_file
        self._deadline = deadline
        super().__init__()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_subscription_url(
            newurl,
            allow_http=self._allow_http,
            allow_file=self._allow_file,
        )
        _validate_resolved_destination(newurl, deadline=self._deadline)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _set_response_read_timeout(response: Any, deadline: _Deadline) -> None:
    """Tighten the active HTTP socket before each read when urllib exposes it."""
    try:
        sock = response.fp.raw._sock
    except AttributeError:
        return
    sock.settimeout(deadline.remaining())


def _read_bounded(
    response,
    max_bytes: int,
    *,
    limit_error: str | None = None,
    deadline: _Deadline | None = None,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        if deadline is not None:
            deadline.remaining()
            _set_response_read_timeout(response, deadline)
        chunk = response.read(min(65536, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise FetchError(limit_error or "subscription exceeds the configured byte limit")
    return b"".join(chunks)


def _decompress_gzip_bounded(
    raw: bytes, max_bytes: int, *, deadline: _Deadline | None = None
) -> bytes:
    """Decompress gzip data without allocating beyond the expanded byte budget."""
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as stream:
            return _read_bounded(
                stream,
                max_bytes,
                limit_error="decompressed subscription exceeds the byte limit",
                deadline=deadline,
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
    client_profile: str = "default",
) -> str:
    deadline = _Deadline(timeout)
    validate_subscription_url(url, allow_http=allow_http, allow_file=allow_file)
    parsed = urlsplit(url)
    if parsed.scheme == "file":
        path = Path(url2pathname(unquote(parsed.path)))
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise FetchError("cannot read local subscription fixture") from exc
        if len(raw) > max_bytes:
            raise FetchError("subscription exceeds the configured byte limit")
    else:
        _validate_resolved_destination(url, deadline=deadline)
        user_agent = _CLIENT_USER_AGENTS.get(client_profile)
        if user_agent is None:
            raise FetchError("unsupported subscription client profile")
        request = urllib.request.Request(
            url,
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip"},
            method="GET",
        )
        context = ssl.create_default_context()
        opener = urllib.request.build_opener(
            # Subscription fetches must never inherit ambient HTTP(S) proxy
            # settings: a proxy would break the validated-DNS-to-socket binding.
            urllib.request.ProxyHandler({}),
            _SafeRedirectHandler(allow_http=allow_http, allow_file=allow_file, deadline=deadline),
            _PinnedHTTPHandler(deadline=deadline),
            _PinnedHTTPSHandler(context=context, deadline=deadline),
        )
        try:
            with opener.open(request, timeout=deadline.remaining()) as response:
                validate_subscription_url(
                    response.geturl(), allow_http=allow_http, allow_file=allow_file
                )
                _validate_resolved_destination(response.geturl(), deadline=deadline)
                raw = _read_bounded(response, max_bytes, deadline=deadline)
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    raw = _decompress_gzip_bounded(raw, max_bytes, deadline=deadline)
        except FetchError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            safe = redact_text(str(exc), [url])
            raise FetchError(f"subscription fetch failed for {redact_url(url)}: {safe}") from exc
    try:
        # Normalize transport line endings so local fixture reads and remote
        # subscriptions have the same deterministic text representation.
        return raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as exc:
        raise FetchError("subscription is not valid UTF-8") from exc


def fetch_https_bytes(url: str, *, timeout: int, max_bytes: int) -> bytes:
    """Read an exact HTTPS entity for release verification, without text normalization.

    Redirects are rejected: the configured client entry must itself serve the
    configuration, and its bearer URL must never be forwarded to another origin.
    Reuse pinned public destinations, TLS verification and bounded reads.
    """

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise FetchError("final entry redirected")

    deadline = _Deadline(timeout)
    validate_subscription_url(url, allow_http=False, allow_file=False)
    _validate_resolved_destination(url, deadline=deadline)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _CLIENT_USER_AGENTS["mihomo"],
            "Accept-Encoding": "gzip",
            "Cache-Control": "no-cache",
        },
        method="GET",
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        NoRedirect(),
        _PinnedHTTPSHandler(context=ssl.create_default_context(), deadline=deadline),
    )
    with opener.open(request, timeout=deadline.remaining()) as response:
        if response.status != 200:
            raise FetchError("final entry did not return HTTP 200")
        raw = _read_bounded(response, max_bytes, deadline=deadline)
        encoding = response.headers.get("Content-Encoding", "").lower()
        if encoding == "gzip":
            raw = _decompress_gzip_bounded(raw, max_bytes, deadline=deadline)
        elif encoding not in {"", "identity"}:
            raise FetchError("final entry returned an unsupported content encoding")
        return raw
