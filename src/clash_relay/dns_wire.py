"""RFC 8484 (application/dns-message) DoH probing for runner-side admission.

Runner-side hostname qualification probes HTTPS DoH endpoints using the
standard wireformat instead of the Cloudflare JSON API, so resolver-specific
JSON API differences (AliDNS, DNSPod, ...) cannot produce false
"unresolvable" verdicts. Every outcome is classified so qualification reports
stay aggregate-only while remaining explainable:

- ``answered``: the *Answer* section carried at least one public record of the
  queried type (Authority/Additional records are never results);
- ``nxdomain`` / ``no_answer`` / ``server_failure`` / ``malformed_response``:
  a real DNS response without a usable answer;
- ``dns_failure`` / ``connect_timeout`` / ``refused`` / ``http_error`` /
  ``transport_error``: the probe never reached a DNS response.

No payload content, hostname, or resolver identity crosses this boundary.
"""

from __future__ import annotations

import base64
import ipaddress
import socket
import struct
import urllib.error
import urllib.parse
import urllib.request

_TYPE_A = 1
_TYPE_AAAA = 28
_RCODE_NOERROR = 0
_RCODE_NXDOMAIN = 3
_MAX_RESPONSE_BYTES = 65536
_FLAGS_RECURSION_DESIRED = 0x0100
_FLAGS_RESPONSE = 0x8000
_COMPRESSION_POINTER = 0xC0
_MAX_POINTER_JUMPS = 64

_ECONNREFUSED = getattr(socket, "ECONNREFUSED", None)
_UNREACHABLE_ERRNOS = frozenset(
    value
    for value in (
        getattr(socket, name, None)
        for name in ("EHOSTUNREACH", "ENETUNREACH", "ENETDOWN", "ENETRESET")
    )
    if value is not None
)

ANSWER_CATEGORIES = frozenset({"answered", "nxdomain", "no_answer", "server_failure"})
TRANSPORT_CATEGORIES = frozenset(
    {"dns_failure", "connect_timeout", "refused", "http_error", "transport_error"}
)


def build_query(hostname: str, qtype: int = _TYPE_A) -> bytes:
    """Build a wireformat query for ``qtype`` with recursion desired set."""

    labels = hostname.rstrip(".").split(".")
    if not hostname or not labels or any(not label for label in labels):
        raise ValueError(f"invalid DNS query name: {hostname!r}")
    question = bytearray()
    for label in labels:
        wire = label.encode("ascii") if label.isascii() else label.encode("idna")
        if not 1 <= len(wire) <= 63:
            raise ValueError(f"invalid DNS query name: {hostname!r}")
        question += bytes([len(wire)]) + wire
    question += b"\x00"
    header = struct.pack("!HHHHHH", 0, _FLAGS_RECURSION_DESIRED, 1, 0, 0, 0)
    return header + bytes(question) + struct.pack("!HH", qtype, 1)


def _public_address(value: bytes) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_global


def _skip_name(payload: bytes, offset: int) -> int:
    """Return the offset just past one (possibly compressed) name.

    Compression pointers are followed with a jump cap; the returned offset
    always advances within the original byte stream.
    """

    jumps = 0
    pos = offset
    end: int | None = None
    while True:
        if pos >= len(payload):
            raise ValueError("truncated name")
        length = payload[pos]
        if length == 0:
            return end if end is not None else pos + 1
        if length & _COMPRESSION_POINTER:
            if pos + 1 >= len(payload):
                raise ValueError("truncated name")
            if end is None:
                end = pos + 2
            target = ((length & 0x3F) << 8) | payload[pos + 1]
            jumps += 1
            if jumps > _MAX_POINTER_JUMPS:
                raise ValueError("compression pointer loop")
            pos = target
            continue
        pos += 1 + length


def parse_response(payload: bytes, *, qtype: int = _TYPE_A) -> tuple[bool, str]:
    """Return (answered, category) for one wireformat DNS response.

    Parsing is strictly bounded by QDCOUNT/ANCOUNT: only Answer-section records
    of the queried type can qualify, and any truncation or bounds violation is
    ``malformed_response`` rather than a silent partial read.
    """

    if len(payload) < 12:
        return False, "malformed_response"
    _ident, flags, qdcount, ancount, _nscount, _arcount = struct.unpack("!HHHHHH", payload[:12])
    if not flags & _FLAGS_RESPONSE:
        return False, "malformed_response"
    rcode = flags & 0x0F
    if rcode == _RCODE_NXDOMAIN:
        return False, "nxdomain"
    if rcode != _RCODE_NOERROR:
        return False, "server_failure"
    offset = 12
    try:
        for _ in range(qdcount):
            offset = _skip_name(payload, offset)
            if offset + 4 > len(payload):
                raise ValueError("truncated question")
            offset += 4
        for _ in range(ancount):
            offset = _skip_name(payload, offset)
            if offset + 10 > len(payload):
                raise ValueError("truncated answer header")
            rtype, _rclass, _ttl, rdlength = struct.unpack("!HHIH", payload[offset : offset + 10])
            offset += 10
            if offset + rdlength > len(payload):
                raise ValueError("truncated rdata")
            rdata = payload[offset : offset + rdlength]
            offset += rdlength
            if rtype == qtype and _public_address(rdata):
                return True, "answered"
    except (ValueError, IndexError, struct.error):
        return False, "malformed_response"
    return False, "no_answer"


def _transport_category(error: BaseException) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return "http_error"
    if isinstance(error, urllib.error.URLError):
        reason = error.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return "connect_timeout"
        if isinstance(reason, ConnectionRefusedError):
            return "refused"
        if isinstance(reason, socket.gaierror):
            return "dns_failure"
        if isinstance(reason, OSError):
            if reason.errno in _UNREACHABLE_ERRNOS:
                return "transport_error"
            if _ECONNREFUSED is not None and reason.errno == _ECONNREFUSED:
                return "refused"
        return "transport_error"
    if isinstance(error, (socket.timeout, TimeoutError)):
        return "connect_timeout"
    if isinstance(error, ConnectionRefusedError):
        return "refused"
    if isinstance(error, socket.gaierror):
        return "dns_failure"
    return "transport_error"


def probe_doh(
    endpoint: str, hostname: str, *, qtype: int = _TYPE_A, timeout: float = 3.0
) -> tuple[bool, str]:
    """Probe one hostname over one RFC 8484 DoH endpoint for ``qtype``."""

    try:
        query = build_query(hostname, qtype)
    except ValueError:
        return False, "transport_error"
    encoded = base64.urlsafe_b64encode(query).rstrip(b"=").decode("ascii")
    request = urllib.request.Request(
        f"{endpoint}?dns={encoded}",
        headers={"Accept": "application/dns-message"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read(_MAX_RESPONSE_BYTES)
    except (OSError, ValueError) as exc:
        return False, _transport_category(exc)
    return parse_response(payload, qtype=qtype)
