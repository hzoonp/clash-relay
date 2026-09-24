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

        tun = runtime.get("tun")
        if tun is not None and str(tun.get("mode", "managed")) != "client":
            output["tun"] = {
                "enable": tun["enabled"],
                "stack": tun["stack"],
                "dns-hijack": list(tun["dns_hijack"]),
                "auto-route": tun["auto_route"],
                "auto-detect-interface": tun["auto_detect_interface"],
                "strict-route": tun["strict_route"],
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
        rendered_dns: dict[str, Any] = {
            "enable": dns["enabled"],
            "enhanced-mode": dns["enhanced_mode"],
            "listen": dns["listen"],
            "nameserver": list(dns["nameservers"]),
            "fallback": list(dns["fallback_nameservers"]),
        }
        if "ipv6" in dns:
            rendered_dns["ipv6"] = dns["ipv6"]
        if "respect_rules" in dns:
            rendered_dns["respect-rules"] = dns["respect_rules"]
        if "default_nameservers" in dns:
            rendered_dns["default-nameserver"] = list(dns["default_nameservers"])
        if "proxy_server_nameservers" in dns:
            rendered_dns["proxy-server-nameserver"] = list(dns["proxy_server_nameservers"])
        if "direct_nameservers" in dns:
            rendered_dns["direct-nameserver"] = list(dns["direct_nameservers"])
        if "nameserver_policy_overrides" in dns:
            rendered_dns["nameserver-policy"] = {
                str(domain): list(resolvers)
                for domain, resolvers in dns["nameserver_policy_overrides"].items()
            }
        if "direct_nameserver_follow_policy" in dns:
            rendered_dns["direct-nameserver-follow-policy"] = dns["direct_nameserver_follow_policy"]
        if "fake_ip_range" in dns:
            rendered_dns["fake-ip-range"] = dns["fake_ip_range"]
        if "fake_ip_filter_mode" in dns:
            rendered_dns["fake-ip-filter-mode"] = dns["fake_ip_filter_mode"]
        if "fake_ip_filter" in dns:
            rendered_dns["fake-ip-filter"] = list(dns["fake_ip_filter"])
        output["dns"] = rendered_dns
        return output
