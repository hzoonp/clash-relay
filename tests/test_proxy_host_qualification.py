from __future__ import annotations

import pytest

from clash_relay.errors import ValidationError
from clash_relay.proxy_host_qualification import (
    _doh_endpoints,
    quarantine_unresolvable_proxy_hosts,
)


def _candidate() -> dict:
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


def _cn_three_net_candidate() -> dict:
    candidate = _candidate()
    candidate["dns"]["proxy-server-nameserver"] = [
        "system",
        "https://dns.alidns.com/dns-query",
        "https://doh.pub/dns-query",
    ]
    return candidate


def _patch_probes(monkeypatch, results):
    """Patch probe_doh to record probed endpoints and yield canned results."""

    probed: list[tuple[str, str]] = []
    sequence = iter(results)

    def probe(endpoint: str, hostname: str):
        probed.append((endpoint, hostname))
        return next(sequence)

    monkeypatch.setattr("clash_relay.proxy_host_qualification.probe_doh", probe)
    return probed


def test_unresolved_hostname_is_quarantined_without_emptying_provider(monkeypatch) -> None:
    probed = _patch_probes(
        monkeypatch, [(False, "nxdomain"), (False, "nxdomain"), (False, "nxdomain")]
    )
    candidate = _candidate()
    report = quarantine_unresolvable_proxy_hosts(candidate)

    assert report["quarantined"] == 1
    assert report["ip_literal_nodes"] == 1
    assert [node["name"] for node in candidate["proxy-providers"]["cr_browsing_jp"]["payload"]] == [
        "ip"
    ]
    assert report["by_source"] == {"other": 1}
    assert report["by_region"] == {"jp": 1}
    assert report["by_protocol"] == {"trojan": 1}
    assert report["by_failure_category"] == {"nxdomain": 1}
    assert "broken.example" not in repr(report)
    assert len(probed) == 3


def test_resolved_hostname_survives_and_disagreement_stays_aggregate(monkeypatch) -> None:
    _patch_probes(monkeypatch, [(True, "answered"), (False, "nxdomain"), (False, "nxdomain")])
    report = quarantine_unresolvable_proxy_hosts(_candidate())

    assert report["resolved"] == 1
    assert report["quarantined"] == 0
    assert report["resolver_disagreement"] == 0
    assert "broken.example" not in repr(report)


def test_nxdomain_with_transport_failure_is_still_dns_confirmed(monkeypatch) -> None:
    """One authoritative NXDOMAIN is a verdict; another resolver timing out
    cannot retract it."""

    _patch_probes(monkeypatch, [(False, "nxdomain"), (False, "connect_timeout")])
    candidate = _cn_three_net_candidate()
    report = quarantine_unresolvable_proxy_hosts(candidate)

    assert report["quarantined"] == 1
    assert report["by_failure_category"] == {"nxdomain": 1}


def test_runner_transport_failure_never_quarantines(monkeypatch) -> None:
    """A hostname whose probes all time out is inconclusive, not node evidence."""

    _patch_probes(
        monkeypatch,
        [
            (True, "answered"),
            (True, "answered"),
            (True, "answered"),
            (False, "connect_timeout"),
            (False, "connect_timeout"),
            (False, "refused"),
        ],
    )
    candidate = _candidate()
    candidate["proxy-providers"]["cr_browsing_jp"]["payload"] = [
        {"name": "host", "type": "trojan", "server": "healthy.example"},
        {"name": "flaky-host", "type": "trojan", "server": "timingout.example"},
        {"name": "ip", "type": "ss", "server": "8.8.4.4"},
    ]
    report = quarantine_unresolvable_proxy_hosts(candidate)

    assert report["status"] == "passed"
    assert report["quarantined"] == 0
    assert report["dns_inconclusive"] == 1
    assert [
        node["server"] for node in candidate["proxy-providers"]["cr_browsing_jp"]["payload"]
    ] == [
        "healthy.example",
        "timingout.example",
        "8.8.4.4",
    ]


def test_server_failure_alone_is_inconclusive(monkeypatch) -> None:
    _patch_probes(monkeypatch, [(False, "server_failure"), (False, "server_failure")])
    report = quarantine_unresolvable_proxy_hosts(_cn_three_net_candidate())

    assert report["quarantined"] == 0
    assert report["dns_inconclusive"] == 1


def test_no_answer_from_every_resolver_is_quarantined(monkeypatch) -> None:
    _patch_probes(monkeypatch, [(False, "no_answer"), (False, "no_answer")])
    report = quarantine_unresolvable_proxy_hosts(_cn_three_net_candidate())

    assert report["quarantined"] == 1
    assert report["by_failure_category"] == {"no_answer": 1}


def test_entirely_inconclusive_stage_fails_closed(monkeypatch) -> None:
    """A runner whose resolvers all fail must not silently ship a candidate."""

    _patch_probes(monkeypatch, [(False, "connect_timeout"), (False, "connect_timeout")])
    candidate = _cn_three_net_candidate()
    candidate["proxy-providers"]["cr_browsing_jp"]["payload"] = [
        {"name": "host", "type": "trojan", "server": "broken.example"}
    ]

    with pytest.raises(ValidationError, match="inconclusive"):
        quarantine_unresolvable_proxy_hosts(candidate)


def test_source_failure_categories_are_reported_per_source(monkeypatch) -> None:
    _patch_probes(
        monkeypatch,
        [(False, "nxdomain"), (False, "nxdomain"), (False, "no_answer"), (False, "no_answer")],
    )
    candidate = _cn_three_net_candidate()
    candidate["proxy-providers"]["cr_browsing_jp"]["payload"] = [
        {"name": "[BROWSING:JP] sub_2/Dead", "type": "vless", "server": "dead.example"},
        {"name": "[BROWSING:JP] sub_2/Dead2", "type": "trojan", "server": "dead2.example"},
        {"name": "ip", "type": "ss", "server": "8.8.4.4"},
    ]

    report = quarantine_unresolvable_proxy_hosts(candidate)

    assert report["by_source"] == {"sub_2": 2}
    # Verdict categories are counted per quarantined node, not per probe.
    assert report["by_source_failure_category"] == {"sub_2": {"nxdomain": 1, "no_answer": 1}}


def test_system_resolver_is_excluded_from_runner_doh_probing(monkeypatch) -> None:
    probed = _patch_probes(monkeypatch, [(True, "answered"), (True, "answered")])
    report = quarantine_unresolvable_proxy_hosts(_cn_three_net_candidate())

    assert [endpoint for endpoint, _ in probed] == [
        "https://dns.alidns.com/dns-query",
        "https://doh.pub/dns-query",
    ]
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
