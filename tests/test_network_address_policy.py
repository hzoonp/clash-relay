from __future__ import annotations

import pytest

from clash_relay.network_address_policy import (
    AddressAdmission,
    classify_ip_address,
    is_global_address,
    require_global_address,
)


@pytest.mark.parametrize(
    ("address", "category"),
    [
        ("8.8.8.8", AddressAdmission.GLOBAL),
        ("::ffff:8.8.8.8", AddressAdmission.GLOBAL),
        ("127.0.0.1", AddressAdmission.LOOPBACK),
        ("::ffff:127.0.0.1", AddressAdmission.LOOPBACK),
        ("10.0.0.1", AddressAdmission.PRIVATE),
        ("::ffff:10.0.0.1", AddressAdmission.PRIVATE),
        ("100.64.0.1", AddressAdmission.SHARED),
        ("::ffff:100.64.0.1", AddressAdmission.SHARED),
        ("192.0.2.1", AddressAdmission.DOCUMENTATION),
        ("2001:db8::1", AddressAdmission.DOCUMENTATION),
    ],
)
def test_address_classification_including_ipv4_mapped_ipv6(
    address: str, category: AddressAdmission
) -> None:
    assert classify_ip_address(address) is category
    assert is_global_address(address) is (category is AddressAdmission.GLOBAL)
    if category is AddressAdmission.GLOBAL:
        require_global_address(address)
    else:
        with pytest.raises(ValueError):
            require_global_address(address)


def test_invalid_and_scoped_addresses_do_not_pass_global_policy() -> None:
    assert not is_global_address("not-an-ip")
    assert classify_ip_address("2606:4700::1111%eth0") is AddressAdmission.OTHER_SPECIAL
