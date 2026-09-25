from __future__ import annotations

import pytest

from clash_relay.errors import ValidationError
from clash_relay.proxy_host_qualification import _doh_endpoints, quarantine_unresolvable_proxy_hosts


def _candidate():
    return {
        "dns": {
            "proxy-server-nameserver": [
                "https://1.1.1.1/dns-query",
                "https://8.8.8.8/dns-query",
                "https://223.5.5.5/dns-query",
            ]
        },
        "proxy-providers": {
            "cr_browsing_jp": {
                "payload": [
                    {"name": "host", "type": "trojan", "server": "broken.example"},
                    {"name": "ip", "type": "ss", "server": "8.8.4.4"},
                ]
            }
        },
    }


def _cn_three_net_candidate():
    candidate = _candidate()
    candidate["dns"]["proxy-server-nameserver"] = [
        "system",
        "https://dns.alidns.com/dns-query",
        "https://doh.pub/dns-query",
    ]
    return candidate


def test_unresolved_hostname_is_quarantined_without_emptying_provider(monkeypatch):
    monkeypatch.setattr("clash_relay.proxy_host_qualification._resolve", lambda *_: (False, False))
    candidate = _candidate()
    report = quarantine_unresolvable_proxy_hosts(candidate)
    assert report["quarantined"] == 1
    assert report["ip_literal_nodes"] == 1
    assert [node["name"] for node in candidate["proxy-providers"]["cr_browsing_jp"]["payload"]] == [
        "ip"
    ]


def test_resolved_hostname_and_resolver_disagreement_are_aggregate_only(monkeypatch):
    answers = iter([(True, False), (False, True), (False, True)])
    monkeypatch.setattr("clash_relay.proxy_host_qualification._resolve", lambda *_: next(answers))
    report = quarantine_unresolvable_proxy_hosts(_candidate())
    assert report["resolved"] == 1
    assert report["resolver_disagreement"] == 1
    assert "broken.example" not in repr(report)


def test_system_resolver_is_excluded_from_runner_doh_probing(monkeypatch):
    """The cn_three_net pool contains `system`; only DoH endpoints are probed."""

    probed = []
    monkeypatch.setattr(
        "clash_relay.proxy_host_qualification._resolve",
        lambda endpoint, *_: (probed.append(endpoint) or (True, False)),
    )
    report = quarantine_unresolvable_proxy_hosts(_cn_three_net_candidate())

    assert probed == ["https://dns.alidns.com/dns-query", "https://doh.pub/dns-query"]
    assert report["resolved"] == 1
    assert report["quarantined"] == 0


def test_system_resolver_alone_fails_closed_without_probe_endpoints():
    candidate = _candidate()
    candidate["dns"]["proxy-server-nameserver"] = ["system"]

    with pytest.raises(ValidationError, match="DoH proxy-server-nameserver"):
        quarantine_unresolvable_proxy_hosts(candidate)


def test_single_doh_resolver_fails_closed():
    candidate = _candidate()
    candidate["dns"]["proxy-server-nameserver"] = ["system", "https://dns.alidns.com/dns-query"]

    with pytest.raises(ValidationError, match="DoH proxy-server-nameserver"):
        quarantine_unresolvable_proxy_hosts(candidate)


def test_doh_endpoint_filter_keeps_https_only_and_drops_malformed_entries():
    assert _doh_endpoints(
        [
            "system",
            "https://dns.alidns.com/dns-query",
            "tls://223.5.5.5",
            "223.5.5.5",
            "https://",
            "https://[bad",
        ]
    ) == ["https://dns.alidns.com/dns-query"]


def test_resolver_request_failure_degrades_to_transport_failure(monkeypatch):
    """A failing DoH request must not crash the qualification stage."""

    def broken_request(*_args, **_kwargs):
        raise ValueError("unknown url type")

    monkeypatch.setattr(
        "clash_relay.proxy_host_qualification.urllib.request.Request", broken_request
    )

    report = quarantine_unresolvable_proxy_hosts(_cn_three_net_candidate())

    assert report["status"] == "passed"
    assert report["quarantined"] == 1
