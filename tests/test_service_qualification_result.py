from __future__ import annotations

import pytest

from clash_relay.errors import ValidationError
from clash_relay.service_qualification import service_qualifications
from clash_relay.service_qualification_result import build_service_qualification_result


def test_registered_services_share_one_aggregate_result_shape() -> None:
    shapes: list[set[str]] = []
    for service in service_qualifications():
        result = build_service_qualification_result(
            service,
            live_tested=4,
            live_qualified=2,
            cache_pass_hits=1,
            cache_fail_hits=1,
            outcomes={"passed": 2, "timeout": 2},
        ).as_dict()
        shapes.append(set(result))
        assert result["service"] == service.label
        assert result["probe_name"] == service.probe_name
        assert result["status"] == "qualified"
        assert result["tested_candidates"] == 6
        assert result["qualified_candidates"] == 3
        assert result["rejected_candidates"] == 3
        assert result["outcomes"] == {"passed": 2, "timeout": 2}

    assert shapes and all(shape == shapes[0] for shape in shapes)


def test_result_shape_contains_only_aggregate_counts() -> None:
    service = service_qualifications()[0]
    result = build_service_qualification_result(
        service,
        live_tested=2,
        live_qualified=0,
        cache_pass_hits=0,
        cache_fail_hits=1,
        outcomes={"timeout": 2},
    ).as_dict()

    assert result["status"] == "rejected"
    assert result["tested_candidates"] == 3
    assert result["qualified_candidates"] == 0
    serialized_keys = set(result)
    assert not ({"nodes", "proxies", "urls", "credentials", "response_bodies"} & serialized_keys)


def test_unstructured_outcome_labels_fail_closed() -> None:
    service = service_qualifications()[0]
    with pytest.raises(ValidationError, match="aggregate-safe"):
        build_service_qualification_result(
            service,
            live_tested=1,
            live_qualified=0,
            cache_pass_hits=0,
            cache_fail_hits=0,
            outcomes={"https://provider.example/raw": 1},
        )
