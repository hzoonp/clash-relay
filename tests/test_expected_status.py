from clash_relay.util import normalize_expected_status


def test_single_expected_status_is_an_integer() -> None:
    assert normalize_expected_status("204") == 204
    assert normalize_expected_status(401) == 401


def test_expected_status_ranges_and_sets_remain_strings() -> None:
    assert normalize_expected_status("200-399") == "200-399"
    assert normalize_expected_status("200/204") == "200/204"
