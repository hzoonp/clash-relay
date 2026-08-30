from __future__ import annotations


def _groups(config: dict) -> dict[str, dict]:
    return {item["name"]: item for item in config["proxy-groups"]}


def test_public_groups_expose_provider_nodes_for_manual_selection(built_candidate) -> None:
    config = built_candidate.config
    groups = _groups(config)
    providers = set(config["proxy-providers"])

    for public in [item for item in config["proxy-groups"] if not item.get("hidden", False)]:
        fallback = groups[public["proxies"][0]]
        if fallback["proxies"] == ["REJECT"]:
            assert "use" not in public
            continue

        expected: list[str] = []
        for child_name in fallback["proxies"]:
            child = groups[child_name]
            for provider_name in child.get("use", []):
                if provider_name not in expected:
                    expected.append(provider_name)

        assert public["use"] == expected
        assert expected
        assert set(expected).issubset(providers)
