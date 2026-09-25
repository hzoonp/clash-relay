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
                    {
                        "name": "[BROWSING:JP] provider_a/host #0000000000",
                        "type": "trojan",
                        "server": "broken.example",
                    },
                    {
                        "name": "[BROWSING:JP] provider_a/ip #0000000000",
                        "type": "ss",
                        "server": "8.8.4.4",
                    },
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


def test_one_unresolvable_hostname_can_contain_two_logical_nodes(monkeypatch) -> None:
    _patch_probes(
        monkeypatch,
        [(False, "nxdomain"), (False, "nxdomain"), (False, "nxdomain")],
    )
    candidate = _candidate()
    candidate["proxy-providers"]["cr_browsing_jp"]["payload"] = [
        {
            "name": "[BROWSING:JP] provider_a/First #0000000001",
            "type": "trojan",
            "server": "shared.example",
            "port": 443,
            "password": "first",
        },
        {
            "name": "[BROWSING:JP] provider_a/Second #0000000002",
            "type": "trojan",
            "server": "shared.example",
            "port": 443,
            "password": "second",
        },
        {
            "name": "[BROWSING:JP] provider_a/Alive #0000000003",
            "type": "ss",
            "server": "8.8.4.4",
            "port": 443,
        },
    ]
    report = quarantine_unresolvable_proxy_hosts(candidate)
    assert report["quarantined"] == 2
    assert report["unique_quarantined_nodes"] == 2
    assert report["unique_quarantined_hostnames"] == 1
    assert report["by_source"] == {"provider_a": 2}


def test_unresolved_hostname_is_quarantined_without_emptying_provider(monkeypatch) -> None:
    probed = _patch_probes(
        monkeypatch, [(False, "nxdomain"), (False, "nxdomain"), (False, "nxdomain")]
    )
    candidate = _candidate()
    report = quarantine_unresolvable_proxy_hosts(candidate)

    assert report["quarantined"] == 1
    assert report["ip_literal_nodes"] == 1
    assert [node["name"] for node in candidate["proxy-providers"]["cr_browsing_jp"]["payload"]] == [
        "[BROWSING:JP] provider_a/ip #0000000000"
    ]
    assert report["by_source"] == {"provider_a": 1}
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


def test_single_negative_with_transport_failures_keeps_node(monkeypatch) -> None:
    """One resolver's NXDOMAIN plus another resolver's transport failure is
    uncorroborated: the node stays (inconclusive), never quarantined."""

    _patch_probes(monkeypatch, [(False, "nxdomain"), (False, "connect_timeout")])
    candidate = _cn_three_net_candidate()
    report = quarantine_unresolvable_proxy_hosts(candidate)

    assert report["quarantined"] == 0
    assert report["dns_inconclusive"] == 1
    assert [
        node["server"] for node in candidate["proxy-providers"]["cr_browsing_jp"]["payload"]
    ] == [
        "broken.example",
        "8.8.4.4",
    ]


def test_two_agreeing_negatives_are_required_to_quarantine(monkeypatch) -> None:
    """Two independent resolvers agreeing on a negative is DNS-confirmed dead."""

    _patch_probes(monkeypatch, [(False, "nxdomain"), (False, "no_answer")])
    report = quarantine_unresolvable_proxy_hosts(_cn_three_net_candidate())

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
        {
            "name": "[BROWSING:JP] provider_a/host #0000000000",
            "type": "trojan",
            "server": "healthy.example",
        },
        {
            "name": "[BROWSING:JP] provider_a/flaky-host #0000000000",
            "type": "trojan",
            "server": "timingout.example",
        },
        {"name": "[BROWSING:JP] provider_a/ip #0000000000", "type": "ss", "server": "8.8.4.4"},
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
        {
            "name": "[BROWSING:JP] provider_a/host #0000000000",
            "type": "trojan",
            "server": "broken.example",
        }
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
        {"name": "[BROWSING:JP] sub_2/Dead #0000000000", "type": "vless", "server": "dead.example"},
        {
            "name": "[BROWSING:JP] sub_2/Dead2 #0000000000",
            "type": "trojan",
            "server": "dead2.example",
        },
        {"name": "[BROWSING:JP] provider_a/ip #0000000000", "type": "ss", "server": "8.8.4.4"},
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


def test_duplicate_hostname_across_providers_probes_once_and_counts_unique(
    monkeypatch,
) -> None:
    """The same physical node replicated into several providers is probed once
    per endpoint and quarantined once as a unique node with multiple runtime
    entries."""

    probed: list[str] = []

    def probe(endpoint: str, hostname: str):
        probed.append((endpoint, hostname))
        return (False, "nxdomain")

    monkeypatch.setattr("clash_relay.proxy_host_qualification.probe_doh", probe)
    candidate = _candidate()
    candidate["dns"]["proxy-server-nameserver"] = [
        "https://dns.alidns.com/dns-query",
        "https://doh.pub/dns-query",
    ]
    candidate["proxy-providers"] = {
        "cr_browsing_jp": {
            "payload": [
                {
                    "name": "[BROWSING:JP] sub_2/One #0000000000",
                    "type": "trojan",
                    "server": "dead.example",
                },
                {"name": "[BROWSING:JP] sub_2/IP #0000000000", "type": "ss", "server": "8.8.4.4"},
            ]
        },
        "cr_general_jp": {
            "payload": [
                {
                    "name": "[GENERAL:ANY] sub_2/One #0000000000",
                    "type": "trojan",
                    "server": "dead.example",
                },
                {"name": "[GENERAL:ANY] sub_2/IP #0000000000", "type": "ss", "server": "8.8.4.4"},
            ]
        },
        "cr_ai_jp": {
            "payload": [
                {
                    "name": "[AI:JP] sub_2/One #0000000000",
                    "type": "trojan",
                    "server": "dead.example",
                },
                {"name": "[AI:JP] sub_2/IP #0000000000", "type": "ss", "server": "8.8.4.4"},
            ]
        },
    }

    report = quarantine_unresolvable_proxy_hosts(candidate)

    # Two endpoints x one unique hostname: exactly two probes.
    assert len(probed) == 2
    assert report["quarantined"] == 3
    assert report["unique_quarantined_nodes"] == 1
    # by_source counts runtime entries; the unique view above deduplicates
    # the same physical node replicated across providers.
    assert report["by_source"] == {"sub_2": 3}
    assert report["by_protocol"] == {"trojan": 3}


def test_cache_evidence_is_never_reused_across_hostnames(monkeypatch) -> None:
    """A quarantined hostname's evidence must not leak into another hostname's
    verdict (regression for the stale-`results` cache bug)."""

    sequence: dict[str, list[tuple[bool, str]]] = {
        "dead.example": [(False, "nxdomain"), (False, "nxdomain")],
        "flaky.example": [(False, "connect_timeout"), (False, "refused")],
    }
    probed: list[str] = []

    def probe(endpoint: str, hostname: str):
        probed.append(hostname)
        return sequence[hostname].pop(0)

    monkeypatch.setattr("clash_relay.proxy_host_qualification.probe_doh", probe)
    candidate = _candidate()
    candidate["dns"]["proxy-server-nameserver"] = [
        "https://dns.alidns.com/dns-query",
        "https://doh.pub/dns-query",
    ]
    candidate["proxy-providers"] = {
        "cr_browsing_jp": {
            "payload": [
                {
                    "name": "[BROWSING:JP] sub_2/Dead #0000000000",
                    "type": "trojan",
                    "server": "dead.example",
                },
                {
                    "name": "[BROWSING:JP] sub_2/Flaky #0000000000",
                    "type": "vless",
                    "server": "flaky.example",
                },
            ]
        }
    }

    report = quarantine_unresolvable_proxy_hosts(candidate)

    # dead.example: two agreeing negatives -> quarantined.
    # flaky.example: transport-only evidence -> inconclusive, kept. The stale
    # cache bug would have counted dead.example's nxdomain responses here and
    # corrupted the stage-level dns_responses accounting.
    assert report["quarantined"] == 1
    assert report["dns_inconclusive"] == 1
    assert [
        node["server"] for node in candidate["proxy-providers"]["cr_browsing_jp"]["payload"]
    ] == ["flaky.example"]
    assert probed.count("dead.example") == 2
    assert probed.count("flaky.example") == 2


def test_ipv6_enabled_candidate_qualifies_through_aaaa(monkeypatch) -> None:
    """With DNS IPv6 enabled, an AAAA-only hostname must resolve (and an A
    probe that returns NOERROR-without-answers must not kill it)."""

    probed: list[int] = []

    def probe(endpoint: str, hostname: str, *, qtype: int = 1):
        probed.append(qtype)
        if qtype == 1:
            return False, "no_answer"
        return True, "answered"

    monkeypatch.setattr("clash_relay.proxy_host_qualification.probe_doh", probe)
    candidate = _candidate()
    candidate["dns"]["ipv6"] = True
    candidate["dns"]["proxy-server-nameserver"] = [
        "https://dns.alidns.com/dns-query",
        "https://doh.pub/dns-query",
    ]
    candidate["proxy-providers"]["cr_browsing_jp"]["payload"] = [
        {
            "name": "[BROWSING:JP] provider_a/host #0000000000",
            "type": "trojan",
            "server": "v6only.example",
        }
    ]

    report = quarantine_unresolvable_proxy_hosts(candidate)

    assert report["resolved"] == 1
    assert report["quarantined"] == 0
    assert probed.count(1) == 2 and probed.count(28) == 2


def test_ipv6_disabled_candidate_keeps_a_only_probing(monkeypatch) -> None:
    probed: list[int] = []

    def probe(endpoint: str, hostname: str, *, qtype: int = 1):
        probed.append(qtype)
        return False, "no_answer"

    monkeypatch.setattr("clash_relay.proxy_host_qualification.probe_doh", probe)
    candidate = _candidate()
    candidate["dns"]["ipv6"] = False
    candidate["dns"]["proxy-server-nameserver"] = [
        "https://dns.alidns.com/dns-query",
        "https://doh.pub/dns-query",
    ]
    candidate["proxy-providers"]["cr_browsing_jp"]["payload"] = [
        {
            "name": "[BROWSING:JP] provider_a/host #0000000000",
            "type": "trojan",
            "server": "a-only.example",
        },
        {"name": "[BROWSING:JP] provider_a/ip #0000000000", "type": "ss", "server": "8.8.4.4"},
    ]

    report = quarantine_unresolvable_proxy_hosts(candidate)

    assert probed == [1, 1]
    assert report["quarantined"] == 1


def test_merge_dual_records_never_downgrades_server_failure() -> None:
    """server_failure + no_answer must stay inconclusive — a resolver failure
    must never be converted into DNS negative evidence."""

    from clash_relay.proxy_host_qualification import (
        _merge_dual_records,
        _verdict,
    )

    merged = _merge_dual_records([(False, "server_failure"), (False, "no_answer")])
    assert merged == (False, "server_failure")

    # Two endpoints each reporting a resolver failure cannot quarantine.
    results = [merged, _merge_dual_records([(False, "server_failure"), (False, "server_failure")])]
    assert _verdict(results) == ("inconclusive", "server_failure")


def test_merge_dual_records_transport_failure_blocks_negative(monkeypatch) -> None:
    """An A-query NXDOMAIN combined with a failed AAAA transport stays
    inconclusive under the strict all-definitive rule."""

    from clash_relay.proxy_host_qualification import _merge_dual_records

    assert _merge_dual_records([(False, "nxdomain"), (False, "connect_timeout")]) == (
        False,
        "connect_timeout",
    )
    assert _merge_dual_records([(False, "nxdomain"), (False, "no_answer")]) == (
        False,
        "nxdomain",
    )


def test_inconclusive_merged_evidence_cannot_join_negative_quorum(monkeypatch) -> None:
    """Two endpoints whose A/AAAA merges are inconclusive never quarantine."""

    sequence = {
        "host.example": [
            (False, "server_failure"),
            (False, "no_answer"),
        ]
    }

    def probe(endpoint: str, hostname: str, *, qtype: int = 1):
        return sequence[hostname].pop(0) if qtype == 1 else (False, "server_failure")

    monkeypatch.setattr("clash_relay.proxy_host_qualification.probe_doh", probe)
    candidate = _candidate()
    candidate["dns"]["ipv6"] = True
    candidate["dns"]["proxy-server-nameserver"] = [
        "https://dns.alidns.com/dns-query",
        "https://doh.pub/dns-query",
    ]
    candidate["proxy-providers"]["cr_browsing_jp"]["payload"] = [
        {
            "name": "[BROWSING:JP] provider_a/host #0000000000",
            "type": "trojan",
            "server": "host.example",
        },
        {"name": "[BROWSING:JP] provider_a/ip #0000000000", "type": "ss", "server": "8.8.4.4"},
    ]

    report = quarantine_unresolvable_proxy_hosts(candidate)

    assert report["quarantined"] == 0
    assert report["dns_inconclusive"] == 1


def test_duplicate_resolver_declaration_is_one_vote() -> None:
    from clash_relay.proxy_host_qualification import _doh_endpoints

    endpoints = _doh_endpoints(
        [
            "https://dns.alidns.com/dns-query",
            "https://dns.alidns.com:443/dns-query",
            "https://DNS.ALIDNS.com/dns-query",
            "https://doh.pub/dns-query",
        ]
    )
    assert endpoints == ["https://dns.alidns.com/dns-query", "https://doh.pub/dns-query"]


def test_duplicated_resolver_cannot_satisfy_two_resolver_quorum() -> None:
    candidate = _candidate()
    candidate["dns"]["proxy-server-nameserver"] = [
        "https://dns.alidns.com/dns-query",
        "https://dns.alidns.com:443/dns-query",
    ]

    with pytest.raises(ValidationError, match="DoH proxy-server-nameserver"):
        quarantine_unresolvable_proxy_hosts(candidate)
