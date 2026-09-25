from __future__ import annotations

import pytest

from clash_relay.errors import ValidationError
from clash_relay.proxy_endpoint_qualification import (
    _admission_tier,
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

    def probe(server: str, port: int) -> tuple[bool, str, int]:
        seen.append((server, port))
        if server == "good.example":
            return True, "answered", 3
        return False, "connect_timeout", 0

    monkeypatch.setattr("clash_relay.proxy_endpoint_qualification._probe_tcp", probe)
    candidate = _candidate()
    report = quarantine_unreachable_tcp_endpoints(candidate)

    assert report["tcp_nodes"] == report["tested"] == 2
    assert report["reachable"] == report["unreachable"] == report["quarantined"] == 1
    assert report["skipped_udp_native"] == 1
    assert report["attempts"] == 3
    assert report["admission_quorum"] == 1
    assert report["robust_endpoints"] == 1
    assert report["reserve_endpoints"] == 0
    assert len(seen) == 2 and all(server != "udp.example" for server, _ in seen)
    assert [row["type"] for row in candidate["proxy-providers"]["cr_browsing_jp"]["payload"]] == [
        "trojan",
        "hysteria2",
    ]
    assert report["by_source"] == {"sub_2": 1}
    assert report["by_region"] == {"jp": 1}
    assert report["by_protocol"] == {"vless": 1}
    assert report["by_failure_category"] == {"connect_timeout": 1}
    assert report["by_source_failure_category"] == {"sub_2": {"connect_timeout": 1}}
    assert "dead.example" not in repr(report)


def test_transient_endpoint_failures_admit_as_reserve(monkeypatch) -> None:
    """1-of-3 successes is flaky but not obviously dead: admit as reserve."""

    attempts: list[int] = []

    def probe(server: str, port: int) -> tuple[bool, str, int]:
        if server == "good.example":
            successes = 2
        elif server == "flaky.example":
            successes = 1
        else:
            successes = 0
        attempts.append(successes)
        if successes:
            return True, "answered", successes
        return False, "connect_timeout", 0

    monkeypatch.setattr("clash_relay.proxy_endpoint_qualification._probe_tcp", probe)
    candidate = _candidate()
    candidate["proxy-providers"]["cr_browsing_jp"]["payload"] = [
        {
            "name": "[BROWSING:JP] sub_2/Good",
            "type": "trojan",
            "server": "good.example",
            "port": 443,
        },
        {
            "name": "[BROWSING:JP] sub_2/Flaky",
            "type": "vless",
            "server": "flaky.example",
            "port": 443,
        },
        {
            "name": "[BROWSING:JP] sub_2/Dead",
            "type": "vmess",
            "server": "dead.example",
            "port": 443,
        },
    ]
    report = quarantine_unreachable_tcp_endpoints(candidate)

    assert report["reachable"] == 2
    assert report["quarantined"] == 1
    assert report["robust_endpoints"] == 0
    assert report["reserve_endpoints"] == 2
    assert _admission_tier(3) == "robust"
    assert _admission_tier(1) == "reserve"
    assert _admission_tier(0) == "quarantined"
    assert [row["server"] for row in candidate["proxy-providers"]["cr_browsing_jp"]["payload"]] == [
        "good.example",
        "flaky.example",
    ]


def test_runner_dns_failure_keeps_stage_qualified_hostname(monkeypatch) -> None:
    """Stage 1 already DNS-qualified the hostname through the candidate's own
    DoH resolvers; a Runner system-DNS failure is inconclusive and must never
    quarantine the node."""

    def probe(server: str, _port: int) -> tuple[bool, str, int]:
        if server == "8.8.4.4":
            return True, "answered", 3
        return False, "dns_failure", 0

    monkeypatch.setattr("clash_relay.proxy_endpoint_qualification._probe_tcp", probe)
    candidate = _candidate()
    candidate["proxy-providers"]["cr_browsing_jp"]["payload"] = [
        {
            "name": "[BROWSING:JP] sub_2/Dead",
            "type": "vless",
            "server": "dead.example",
            "port": 443,
        },
        {"name": "[BROWSING:JP] sub_2/Alive", "type": "ss", "server": "8.8.4.4", "port": 443},
    ]
    report = quarantine_unreachable_tcp_endpoints(candidate)

    assert report["status"] == "passed"
    assert report["quarantined"] == 0
    assert report["dns_inconclusive"] == 1
    assert report["reserve_endpoints"] == 1
    assert [row["server"] for row in candidate["proxy-providers"]["cr_browsing_jp"]["payload"]] == [
        "dead.example",
        "8.8.4.4",
    ]


def test_multi_address_hostname_counts_attempts_not_addresses(monkeypatch) -> None:
    import socket

    connects: list[tuple[str, int]] = []

    class HealthySocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def settimeout(self, _timeout):
            pass

        def connect(self, target):
            connects.append(target)
            return None

    addresses = [
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.35", 443)),
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: addresses)
    monkeypatch.setattr(socket, "socket", lambda *_args: HealthySocket())

    admitted, category, successes = _probe_tcp("multi.example", 443)

    # Two addresses per attempt must count as ONE successful attempt.
    assert (admitted, category, successes) == (True, "answered", 3)
    assert len(connects) == 6


def test_multi_address_hostname_partial_attempt_success_is_reserve(monkeypatch) -> None:
    import socket

    attempt = {"n": 0}

    class FlakySocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def settimeout(self, _timeout):
            pass

        def connect(self, _target):
            attempt["n"] += 1
            if attempt["n"] in {3, 4}:  # every connect of the second attempt fails
                raise TimeoutError

    addresses = [
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.35", 443)),
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: addresses)
    monkeypatch.setattr(socket, "socket", lambda *_args: FlakySocket())

    admitted, _category, successes = _probe_tcp("flaky.example", 443)

    # Attempts 1 and 3 succeed (each via at least one address); attempt 2 fails.
    assert (admitted, successes) == (True, 2)
    assert _admission_tier(successes) == "reserve"


def test_all_dead_provider_fails_closed_without_mutating_candidate(monkeypatch) -> None:
    monkeypatch.setattr(
        "clash_relay.proxy_endpoint_qualification._probe_tcp",
        lambda *_: (False, "refused", 0),
    )
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


def test_tcp_probe_requires_public_ip_and_retries_three_times(monkeypatch) -> None:
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
    assert _probe_tcp("example.invalid", 443) == (False, "connect_timeout", 0)
    assert attempts == [("8.8.8.8", 443)] * 3


def test_tcp_probe_classifies_refused_and_dns_failure(monkeypatch) -> None:
    import socket

    class RefusedSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def settimeout(self, _timeout):
            pass

        def connect(self, _target):
            raise ConnectionRefusedError

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 443))],
    )
    monkeypatch.setattr(socket, "socket", lambda *_args: RefusedSocket())
    assert _probe_tcp("refused.example", 443) == (False, "refused", 0)

    def fail_getaddrinfo(*_args, **_kwargs):
        raise socket.gaierror

    monkeypatch.setattr(socket, "getaddrinfo", fail_getaddrinfo)
    assert _probe_tcp("unresolvable.example", 443) == (False, "dns_failure", 0)


def test_tcp_probe_counts_successes_across_attempts(monkeypatch) -> None:
    import socket

    connects = {"count": 0}

    class FlakySocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def settimeout(self, _timeout):
            pass

        def connect(self, _target):
            connects["count"] += 1
            if connects["count"] == 2:
                raise TimeoutError

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 443))],
    )
    monkeypatch.setattr(socket, "socket", lambda *_args: FlakySocket())
    admitted, category, successes = _probe_tcp("flaky.example", 443)
    assert (admitted, category, successes) == (True, "answered", 2)
