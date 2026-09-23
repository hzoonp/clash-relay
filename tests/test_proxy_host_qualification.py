from __future__ import annotations

from clash_relay.proxy_host_qualification import quarantine_unresolvable_proxy_hosts


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
