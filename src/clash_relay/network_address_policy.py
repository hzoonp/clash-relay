"""One address policy for subscription destinations and proxy evidence.

An IPv4-mapped IPv6 address inherits the IPv4 address's classification. This
also keeps the decision stable across Python versions whose IPv6 special-use
tables differ. Callers that allow private proxy hosts can bypass this policy
at admission; subscription destinations always require a global address.
"""

from __future__ import annotations

import ipaddress
from enum import Enum


class AddressAdmission(str, Enum):
    GLOBAL = "global"
    LOOPBACK = "loopback"
    PRIVATE = "private"
    LINK_LOCAL = "link_local"
    SHARED = "shared"
    MULTICAST = "multicast"
    RESERVED = "reserved"
    UNSPECIFIED = "unspecified"
    DOCUMENTATION = "documentation"
    OTHER_SPECIAL = "other_special"


_SHARED = ipaddress.ip_network("100.64.0.0/10")
_DOCUMENTATION = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("2001:db8::/32"),
)


def classify_ip_address(
    value: str | bytes | ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> AddressAdmission:
    """Classify an IP literal; malformed values raise ``ValueError``."""
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.scope_id is not None:
        return AddressAdmission.OTHER_SPECIAL
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address.is_loopback:
        return AddressAdmission.LOOPBACK
    if address.is_unspecified:
        return AddressAdmission.UNSPECIFIED
    if address.is_link_local:
        return AddressAdmission.LINK_LOCAL
    if address.version == 4 and address in _SHARED:
        return AddressAdmission.SHARED
    if address.is_multicast:
        return AddressAdmission.MULTICAST
    if any(address in network for network in _DOCUMENTATION if address.version == network.version):
        return AddressAdmission.DOCUMENTATION
    if address.is_private:
        return AddressAdmission.PRIVATE
    if address.is_reserved:
        return AddressAdmission.RESERVED
    if address.is_global:
        return AddressAdmission.GLOBAL
    return AddressAdmission.OTHER_SPECIAL


def is_global_address(value: str | bytes | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return False for malformed addresses and every non-global category."""
    try:
        return classify_ip_address(value) is AddressAdmission.GLOBAL
    except ValueError:
        return False


def require_global_address(
    value: str | bytes | ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> None:
    """Reject malformed and non-global IP addresses."""
    if not is_global_address(value):
        raise ValueError("address must be a globally reachable IP literal")
