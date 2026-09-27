# Client / DNS verification matrix

This is the canonical manual client verification contract for production `clash-relay` profiles. GitHub Actions validates the repository, generated candidate, and Mihomo cores; it does **not** claim to emulate Windows or Android FlClash, carrier networks, Wi-Fi transitions, or application authentication flows.

## Base matrix

Every release candidate intended to change routing, DNS, TUN, regional selection, or client compatibility should be checked across the Cartesian product below when the environment is available:

| Dimension | Required values |
| --- | --- |
| Client | FlClash Windows; FlClash Android |
| Mode | System Proxy; TUN |
| Network | Wi-Fi; mobile hotspot; China Telecom; China Unicom; China Mobile |

That is 20 base client/mode/network combinations. A result may be recorded as unavailable when the physical network is not available; unavailable is not equivalent to passed.

## Required checks

| Check ID | Pass criterion |
| --- | --- |
| `profile_refresh` | Profile refresh/reload succeeds without parse or startup failure. |
| `tun_dns_hijack` | In TUN mode, DNS interception is active and ordinary client DNS requests do not bypass the managed path. |
| `fake_ip_compatibility` | Fake-IP remains active while declared narrow compatibility exceptions resolve without application breakage. |
| `direct_dns` | DIRECT destinations resolve and connect without proxy-resolver dependency. |
| `proxy_server_dns` | Proxy hostnames resolve through the managed proxy-server resolver path without mass timeout regression. |
| `cn_three_net` | Mainland three-network policy resolves and routes under the intended resolver/routing contract. |
| `six_public_groups` | 代理选择、网页浏览、人工智能、流媒体、消息通讯、下载流量 are present and usable. |
| `uk_region` | UK appears only where declared and is selectable/health-checked like other browsing/general regions. |
| `ai_services` | OpenAI, Claude, and Gemini use their qualified AI routing surfaces without DNS leakage into broad compatibility rules. |
| `web_browsing` | Representative HTTPS browsing succeeds and automatic regional selection remains responsive. |
| `streaming` | Streaming traffic reaches the declared streaming selector without falling through to an unrelated source. |
| `messaging` | Messaging traffic reaches the messaging selector and remains usable. |
| `download` | Download traffic reaches the download selector and does not consume subscription_1-only inventory. |
| `ipv4_ipv6` | Expected IPv4/IPv6 behavior is stable for the tested network; failures are classified by address family. |
| `network_transition` | Switching Wi-Fi ↔ mobile/hotspot does not leave stale DNS or unusable selector state after recovery. |
| `carrier_auth` | Carrier authentication flows covered by the narrow compatibility list work in TUN mode. |
| `banking_auth` | Banking applications covered by the narrow compatibility list work in TUN mode without broadening Fake-IP exclusions. |

## Evidence rules

Record aggregate outcomes only: client, mode, network class, check ID, pass/fail/unavailable, and a coarse failure category such as DNS, TCP connect, TLS, HTTP probe, route selection, or application authentication.

Do not commit subscription URLs, node names, proxy servers, ports, credentials, generated production configuration, or screenshots containing those values.

Repository automation owns static and Mihomo-core evidence. This matrix owns real-client evidence. A GitHub Runner success must never be substituted for a missing FlClash/network result.
