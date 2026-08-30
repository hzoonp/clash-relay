from __future__ import annotations


def _groups(config: dict) -> dict[str, dict]:
    return {item["name"]: item for item in config["proxy-groups"]}


def _providers_from_anchor(groups: dict[str, dict], anchor: str) -> list[str]:
    expected: list[str] = []
    pending = [anchor]
    visited: set[str] = set()
    while pending:
        name = pending.pop(0)
        if name in visited:
            continue
        visited.add(name)
        group = groups.get(name)
        if group is None:
            continue
        for provider_name in group.get("use", []):
            if provider_name not in expected:
                expected.append(provider_name)
        pending.extend(item for item in group.get("proxies", []) if item in groups)
    return expected


def test_public_groups_expose_provider_nodes_for_manual_selection(built_candidate) -> None:
    config = built_candidate.config
    groups = _groups(config)
    providers = set(config["proxy-providers"])

    for public in [item for item in config["proxy-groups"] if not item.get("hidden", False)]:
        assert len(public["proxies"]) == 1
        expected = _providers_from_anchor(groups, public["proxies"][0])
        if not expected:
            assert "use" not in public
            continue

        assert public["use"] == expected
        assert set(expected).issubset(providers)
