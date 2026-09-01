from __future__ import annotations

from clash_relay.source_health import evaluate_source_health, inventory_snapshot


def _proxy(source: str, index: int) -> dict:
    return {
        "name": f"[TEST] {source}/node-{index} #deadbeef{index:02d}",
        "type": "ss",
        "server": f"198.51.100.{index + 1}",
        "port": 10000 + index,
        "cipher": "aes-128-gcm",
        "password": f"fixture-{source}-{index}",
        "udp": True,
    }


def _config(source_counts: dict[str, int], regions: dict[str, dict[str, list[int]]] | None = None) -> dict:
    providers: dict[str, dict] = {}
    all_payload: list[dict] = []
    for source, count in source_counts.items():
        all_payload.extend(_proxy(source, index) for index in range(count))
    providers["cr_general_ANY"] = {"type": "inline", "payload": all_payload}
    for source, by_region in (regions or {}).items():
        for region, indexes in by_region.items():
            provider = providers.setdefault(
                f"cr_browsing_{region}", {"type": "inline", "payload": []}
            )
            provider["payload"].extend(_proxy(source, index) for index in indexes)
    return {"proxy-providers": providers}


def test_inventory_snapshot_deduplicates_same_runtime_node_across_pools() -> None:
    config = _config(
        {"subscription_1": 4},
        {"subscription_1": {"US": [0, 1, 2], "SG": [3]}},
    )

    snapshot = inventory_snapshot(config)

    assert snapshot.total_nodes == 4
    assert snapshot.sources == {"subscription_1": 4}
    assert snapshot.regions["subscription_1"] == {"SG": 1, "US": 3}


def test_source_health_rejects_large_active_source_drop() -> None:
    previous = _config({"subscription_2": 10})
    candidate = _config({"subscription_2": 3})

    report = evaluate_source_health(
        previous,
        candidate,
        declared_sources={"subscription_2"},
        browsing_sources={"subscription_2"},
    )

    assert report["status"] == "rejected"
    assert any(item["kind"] == "source_inventory_drop" for item in report["violations"])


def test_source_health_allows_explicitly_removed_source() -> None:
    previous = _config({"subscription_1": 4, "subscription_2": 20})
    candidate = _config({"subscription_1": 4})

    report = evaluate_source_health(
        previous,
        candidate,
        declared_sources={"subscription_1"},
        browsing_sources={"subscription_1"},
    )

    assert report["status"] == "healthy"
    assert report["planned_removed_sources"] == ["subscription_2"]


def test_source_health_rejects_protected_browsing_region_disappearance() -> None:
    previous = _config(
        {"subscription_1": 6},
        {"subscription_1": {"US": [0, 1, 2], "SG": [3, 4, 5]}},
    )
    candidate = _config(
        {"subscription_1": 6},
        {"subscription_1": {"SG": [0, 1, 2, 3, 4, 5]}},
    )

    report = evaluate_source_health(
        previous,
        candidate,
        declared_sources={"subscription_1"},
        browsing_sources={"subscription_1"},
    )

    assert report["status"] == "rejected"
    assert {
        (item.get("kind"), item.get("source"), item.get("region"))
        for item in report["violations"]
    } >= {("protected_region_disappeared", "subscription_1", "US")}
