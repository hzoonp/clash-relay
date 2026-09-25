from __future__ import annotations

import pytest

from clash_relay.errors import ValidationError
from clash_relay.proxy_endpoint_qualification import (
    _probe_tcp,
    accelerate_client_health_checks,
    quarantine_unreachable_tcp_endpoints,
)


def _candidate() -> dict:
    return {
        "proxy-providers": {
            "cr_browsing_jp": {
                "payload": [
                    {
                        "name": "[BROWSING:JP] sub_2/Good",
                        "type": "trojan",
                        "server": "good.example",
                        "port": 443,
                    },
                    {
                        "name": "[BROWSING:JP] sub_2/Dead",
                        "type": "vless",
                        "server": "dead.example",
                        "port": 443,
                    },
                    {
                        "name": "[BROWSING:JP] sub_2/UDP",
                        "type": "hysteria2",
                        "server": "udp.example",
                        "port": 443,
                    },
                ]
            }
        },
        "proxy-groups": [
            {"name": "日本节点", "type": "url-test", "url": "https://www.gstatic.com/generate_204"},
            {
                "name": "网页 · 日本",
                "type": "fallback",
                "url": "https://www.gstatic.com/generate_204",
            },
            {
                "name": "__CR_BROWSING_JP_STABLE_AUTO",
                "type": "url-test",
                "url": "https://www.gstatic.com/generate_204",
            },
            {
                "name": "__CR_AUTO_BROWSING_KR",
                "type": "url-test",
                "url": "https://www.gstatic.com/generate_204",
                "use": ["cr_browsing_kr"],
            },
            {"name": "AI · 日本", "type": "fallback", "url": "https://chatgpt.com/"},
            {"name": "自动选择", "type": "url-test", "url": "https://www.gstatic.com/generate_204"},
        ],
    }


def test_dead_tcp_entry_is_quarantined_while_reachable_and_udp_siblings_survive(
    monkeypatch,
) -> None:
    seen: list[tuple[str, int]] = []

    def probe(server: str, port: int) -> bool:
        seen.append((server, port))
        return server == "good.example"

    monkeypatch.setattr("clash_relay.proxy_endpoint_qualification._probe_tcp", probe)
    candidate = _candidate()
    report = quarantine_unreachable_tcp_endpoints(candidate)

    assert report["tcp_nodes"] == report["tested"] == 2
    assert report["reachable"] == report["unreachable"] == report["quarantined"] == 1
    assert report["skipped_udp_native"] == 1
    assert len(seen) == 2 and all(server != "udp.example" for server, _ in seen)
    assert [row["type"] for row in candidate["proxy-providers"]["cr_browsing_jp"]["payload"]] == [
        "trojan",
        "hysteria2",
    ]
    assert report["by_source"] == {"sub_2": 1}
    assert report["by_region"] == {"jp": 1}
    assert report["by_protocol"] == {"vless": 1}
    assert "dead.example" not in repr(report)


def test_all_dead_provider_fails_closed_without_mutating_candidate(monkeypatch) -> None:
    monkeypatch.setattr("clash_relay.proxy_endpoint_qualification._probe_tcp", lambda *_: False)
    candidate = _candidate()
    candidate["proxy-providers"]["cr_browsing_jp"]["payload"] = candidate["proxy-providers"][
        "cr_browsing_jp"
    ]["payload"][:2]
    with pytest.raises(ValidationError, match="empty a proxy provider"):
        quarantine_unreachable_tcp_endpoints(candidate)
    assert len(candidate["proxy-providers"]["cr_browsing_jp"]["payload"]) == 2


def test_client_health_retries_are_bounded_without_touching_ai() -> None:
    candidate = _candidate()
    assert accelerate_client_health_checks(candidate) == 5
    groups = {group["name"]: group for group in candidate["proxy-groups"]}
    assert groups["日本节点"]["max-failed-times"] == 2
    assert groups["网页 · 日本"]["max-failed-times"] == 1
    assert groups["__CR_BROWSING_JP_STABLE_AUTO"]["max-failed-times"] == 1
    assert groups["__CR_AUTO_BROWSING_KR"]["max-failed-times"] == 1
    assert groups["自动选择"]["max-failed-times"] == 2
    assert "max-failed-times" not in groups["AI · 日本"]


def test_udp_only_inventory_is_reported_without_tcp_probe(monkeypatch) -> None:
    monkeypatch.setattr(
        "clash_relay.proxy_endpoint_qualification._probe_tcp",
        lambda *_: pytest.fail("UDP-native nodes must not receive a TCP probe"),
    )
    candidate = _candidate()
    candidate["proxy-providers"]["cr_browsing_jp"]["payload"] = [
        candidate["proxy-providers"]["cr_browsing_jp"]["payload"][2]
    ]
    report = quarantine_unreachable_tcp_endpoints(candidate)
    assert report["tested"] == 0
    assert report["skipped_udp_native"] == 1


def test_tcp_probe_requires_public_ip_and_retries_twice(monkeypatch) -> None:
    import socket

    attempts: list[tuple[str, int]] = []

    class FailedSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def settimeout(self, _timeout):
            pass

        def connect(self, target):
            attempts.append(target)
            raise TimeoutError

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 443))],
    )
    monkeypatch.setattr(socket, "socket", lambda *_args: FailedSocket())
    assert _probe_tcp("example.invalid", 443) is False
    assert attempts == [("8.8.8.8", 443), ("8.8.8.8", 443)]
