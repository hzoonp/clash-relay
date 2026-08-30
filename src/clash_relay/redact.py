"""Safe diagnostic redaction helpers."""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit


_URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
_URL_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:token|access_token|apikey|api_key|key|auth|secret)=)[^&#\s]+"
)
_FIELD_SECRET = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key)\s*[:=]\s*[^\s,;]+"
)
_AUTH_HEADER = re.compile(r"(?i)\bAuthorization\s*:\s*[^\r\n]+")
_USERINFO = re.compile(r"(?i)(https?://)[^/@\s]+@")
_WORDISH_SECRET = re.compile(r"^[A-Za-z0-9_.-]+$")


def redact_url(value: str) -> str:
    """Remove userinfo and URL details while retaining a diagnostic origin."""

    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<redacted-url>"
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "<redacted-url>"
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, "/<redacted>", "<redacted>", "<redacted>"))


def _replace_known_secret(text: str, secret: str) -> str:
    if not secret:
        return text
    if _WORDISH_SECRET.fullmatch(secret):
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_]){re.escape(secret)}(?![A-Za-z0-9_])"
        )
        return pattern.sub("<redacted>", text)
    return text.replace(secret, "<redacted>")


def redact_text(text: str, secrets: list[str] | tuple[str, ...] = ()) -> str:
    """Redact injected values and common credential-shaped text without logging them."""

    # Structured patterns go first. A short injected value such as ``pass`` must
    # not mutate the word ``password`` before its labelled value is removed.
    output = _AUTH_HEADER.sub("Authorization: <redacted>", text)
    output = _FIELD_SECRET.sub(lambda m: f"{m.group(1)}=<redacted>", output)
    output = _URL_QUERY_SECRET.sub(lambda m: f"{m.group(1)}<redacted>", output)
    output = _USERINFO.sub(r"\1<redacted>@", output)

    for secret in sorted({value for value in secrets if value}, key=len, reverse=True):
        output = _replace_known_secret(output, secret)
    return _URL_RE.sub(lambda m: redact_url(m.group(0)), output)
