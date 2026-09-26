from __future__ import annotations

import json

from clash_relay.health_check_inventory import health_check_inventory


def test_inventory_counts_duplicate_providers_and_nested_checks_without_secrets():
    proxy = {"name": "PRIVATE_NODE", "server": "PRIVATE_HOST", "password": "PRIVATE_TOKEN"}
    provider = {
        "payload": [proxy],
        "health-check": {"enable": True, "url": "https://PRIVATE_URL", "interval": 300},
    }
    config = {
        "proxy-providers": {"PRIVATE_A": provider, "PRIVATE_B": provider},
        "proxy-groups": [
            {
                "name": "PRIVATE_REGION",
                "type": "url-test",
                "url": "https://PRIVATE_URL",
                "use": ["PRIVATE_A", "PRIVATE_B"],
                "interval": 300,
            },
            {
                "name": "PRIVATE_AUTO",
                "type": "url-test",
                "url": "https://PRIVATE_URL",
                "proxies": ["PRIVATE_REGION"],
                "interval": 600,
                "lazy": False,
            },
        ],
    }
    report = health_check_inventory(config)
    assert report["provider_checks"] == 2
    assert report["group_checks"] == 2
    assert report["expanded_target_slots"] == 6
    assert report["unique_checked_provider_nodes"] == 1
    assert report["repeated_provider_node_occurrences"] == 1
    assert report["estimated_periodic_requests_per_hour"] == 60
    assert report["lazy_checks"] == 3
    assert report["checks_with_incomplete_estimate"] == 0
    assert "PRIVATE" not in json.dumps(report)


def test_inventory_reports_unknown_payloads_intervals_and_cycles():
    report = health_check_inventory(
        {
            "proxy-providers": {"external": {"health-check": {"enable": True, "interval": 300}}},
            "proxy-groups": [
                {
                    "name": "loop",
                    "type": "fallback",
                    "url": "https://example.test",
                    "proxies": ["loop"],
                    "use": ["external"],
                }
            ],
        }
    )
    assert report["checks_with_incomplete_estimate"] == 2
    assert report["estimated_periodic_requests_per_hour"] == 0
    assert report["measurement_kind"] == "static_inventory"


def test_real_fixture_inventory_is_aggregate_only(built_candidate):
    report = health_check_inventory(built_candidate.config)
    assert report["provider_checks"] > 0
    assert report["estimated_periodic_requests_per_hour"] > 0
    assert report["checks_with_incomplete_estimate"] == 0
    encoded = json.dumps(report)
    for provider in built_candidate.config["proxy-providers"].values():
        for node in provider["payload"]:
            assert node["server"] not in encoded
