"""Render client-facing Mihomo runtime settings from the public config model."""

from __future__ import annotations

from typing import Any


class RuntimeConfigRenderer:
    """Own the config-model -> Mihomo runtime-settings projection."""

    def render(self, config: dict[str, Any]) -> dict[str, Any]:
        runtime = config["runtime"]
        dns = runtime["dns"]
        dns_mode = str(dns.get("mode", "managed"))

        profile: dict[str, Any] = {
            "store-selected": runtime["profile"]["store_selected"],
        }
        output: dict[str, Any] = {
            "mixed-port": runtime["mixed_port"],
            "allow-lan": runtime["allow_lan"],
            "bind-address": runtime["bind_address"],
            "mode": runtime["mode"],
            "log-level": runtime["log_level"],
            "ipv6": runtime["ipv6"],
            "unified-delay": runtime["unified_delay"],
            "tcp-concurrent": runtime["tcp_concurrent"],
            "profile": profile,
        }

        sniffer = runtime.get("sniffer")
        if sniffer is not None:
            sniff = sniffer["sniff"]
            output["sniffer"] = {
                "enable": sniffer["enabled"],
                "force-dns-mapping": sniffer["force_dns_mapping"],
                "parse-pure-ip": sniffer["parse_pure_ip"],
                "sniff": {
                    "HTTP": {
                        "ports": list(sniff["http"]["ports"]),
                        "override-destination": sniff["http"]["override_destination"],
                    },
                    "TLS": {"ports": list(sniff["tls"]["ports"])},
                    "QUIC": {"ports": list(sniff["quic"]["ports"])},
                },
            }

        if dns_mode == "client":
            return output

        profile["store-fake-ip"] = runtime["profile"]["store_fake_ip"]
        output["dns"] = {
            "enable": dns["enabled"],
            "enhanced-mode": dns["enhanced_mode"],
            "listen": dns["listen"],
            "nameserver": list(dns["nameservers"]),
            "fallback": list(dns["fallback_nameservers"]),
        }
        return output
