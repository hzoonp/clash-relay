from __future__ import annotations

import pytest

from clash_relay.errors import ValidationError
from clash_relay.service_qualification import service_qualifications
from clash_relay.service_qualification_result import (
    build_service_qualification_result,
    service_qualification_results,
)


def test_registered_services_share_one_aggregate_result_shape() -> None:
    shapes: list[set[str]] = []
    for service in service_qualifications():
        result = build_service_qualification_result(
            service,
            live_tested=4,
            live_qualified=2,
            cache_pass_hits=1,
            cache_fail_hits=1,
            qualified_regions=2,
            outcomes={"passed": 2, "timeout": 2},
        ).as_dict()
        shapes.append(set(result))
        assert result["service"] == service.label
        assert result["probe_name"] == service.probe_name
        assert result["status"] == "qualified"
        assert result["tested_candidates"] == 6
        assert result["qualified_candidates"] == 3
        assert result["qualified_regions"] == 2
        assert result["rejected_candidates"] == 3
        assert result["outcomes"] == {"passed": 2, "timeout": 2}

    assert shapes and all(shape == shapes[0] for shape in shapes)


def test_ai_probe_diagnostics_project_without_provider_branches() -> None:
    probes = {
        service.probe_name: {
            "live_tested_nodes": 3,
            "cache_pass_hits": 1,
            "cache_fail_hits": 1,
            "qualified_nodes": 2,
            "outcomes": {"passed": 1, "timeout": 2},
        }
        for service in service_qualifications()
    }
    service_country_groups = {
        service.label: {"region-a": 1, "region-b": 0} for service in service_qualifications()
    }
    results = service_qualification_results(
        {
            "diagnostics": {"probes": probes},
            "service_country_groups": service_country_groups,
        }
    )

    assert set(results) == {service.label for service in service_qualifications()}
    assert {tuple(row) for row in results.values()} == {
        (
            "service",
            "probe_name",
            "status",
            "tested_candidates",
            "qualified_candidates",
            "qualified_regions",
            "rejected_candidates",
            "live_tested_candidates",
            "live_qualified_candidates",
            "cache_pass_hits",
            "cache_fail_hits",
            "outcomes",
        )
    }
    assert all(row["tested_candidates"] == 5 for row in results.values())
    assert all(row["qualified_candidates"] == 2 for row in results.values())
    assert all(row["qualified_regions"] == 1 for row in results.values())


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
    assert result["qualified_regions"] == 0
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


def test_malformed_regional_counts_fail_closed() -> None:
    service = service_qualifications()[0]
    probes = {
        item.probe_name: {
            "live_tested_nodes": 1,
            "cache_pass_hits": 0,
            "cache_fail_hits": 0,
            "qualified_nodes": 1,
            "outcomes": {"passed": 1},
        }
        for item in service_qualifications()
    }
    with pytest.raises(ValidationError, match="regional qualified candidates"):
        service_qualification_results(
            {
                "diagnostics": {"probes": probes},
                "service_country_groups": {service.label: {"region-a": -1}},
            }
        )
